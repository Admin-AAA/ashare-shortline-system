"""直连 Tushare stk_auction 回填集合竞价历史（幂等：删当日再插，按 code 去重）。

为何不走 run_auxiliary_application：①stk_auction 经 promax 回源单日 ~8-10s，
orchestration 额外开销大；②官方偶发同一 (code,trade_date) 返回多行，adapter 未去重
会触发 "duplicate natural key in one batch" 整日回滚。这里直连、keepfirst 去重、
bulk insert，专用于把 auction_data 补到双窗口做因子研究。

用法：
  python scripts/backfill_auction_direct.py --start 2025-01-01 --end 2026-09-10
"""
from __future__ import annotations
import argparse, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from app.db.session import SessionLocal
from app.models.tables import AuctionData


def trade_dates(db, s, e):
    return [str(r) for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE trade_date>=:s AND trade_date<=:e ORDER BY trade_date"),
        {"s": s, "e": e}).scalars().all()]


def _f(v):
    try:
        return float(v) if v is not None and v == v else None
    except Exception:
        return None


def existing_count(db, d: str) -> int:
    return db.execute(text("SELECT COUNT(*) FROM auction_data WHERE trade_date=:d"), {"d": d}).scalar() or 0


def _stock_codes(db) -> set:
    """股票主表代码集合。

    为什么需要：`stk_auction` 返回的是全市场**含非股票**标的，且会出现
    「同一 6 位数字尾号分属沪深两市」的行（如 198034.SH / 198034.SZ，债/逆回购类）。
    去掉交易所后缀后它们会塌成同一个 `code`，而 `auction_data` 的唯一键是
    (code, trade_date) → 同批内自撞 IntegrityError，整日回滚（原脚本每日都失败）。
    这里只保留股票主表内的代码，并按派生 code 去重，保留首行。
    """
    rows = db.execute(text("SELECT code FROM stocks")).scalars().all()
    return {str(r) for r in rows}


def backfill_day(db, pro, d: str, retries: int = 3) -> int:
    df = None
    for attempt in range(retries):
        df = pro.stk_auction(trade_date=d.replace("-", ""))
        if df is not None and len(df) > 0:
            break
        time.sleep(2.0 * (attempt + 1))  # 限速 circuit_open 会瞬时空返回，退避重试
    if df is None or len(df) == 0:
        return 0
    df = df.drop_duplicates(subset=["ts_code"], keep="first")
    stock_codes = _stock_codes(db)
    seen: set = set()
    objs = []
    for _, r in df.iterrows():
        code = str(r["ts_code"]).split(".")[0]
        if len(code) != 6 or not code.isdigit():
            continue
        if code not in stock_codes:  # 排除同尾号异所的债/逆回购等非股票
            continue
        if code in seen:             # 同 code 多所 → 表内唯一键 (code, trade_date) 只容一行
            continue
        seen.add(code)
        objs.append(AuctionData(
            code=code, trade_date=d, vol=_f(r.get("vol")), price=_f(r.get("price")),
            amount=_f(r.get("amount")), pre_close=_f(r.get("pre_close")),
            turnover_rate=_f(r.get("turnover_rate")), volume_ratio=_f(r.get("volume_ratio")),
            float_share=_f(r.get("float_share")),
        ))
    db.execute(text("DELETE FROM auction_data WHERE trade_date=:d"), {"d": d})
    db.bulk_save_objects(objs); db.commit()
    return len(objs)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--start", required=True); ap.add_argument("--end", required=True)
    ap.add_argument("--sleep", type=float, default=0.1)
    args = ap.parse_args()
    from app.services.tushare_client import get_pro
    db = SessionLocal(); pro = get_pro()
    dates = trade_dates(db, args.start, args.end)
    print(f"auction days={len(dates)} {dates[0] if dates else '-'}..{dates[-1] if dates else '-'}", flush=True)
    total = 0; t0 = time.time()
    try:
        for i, d in enumerate(dates):
            time.sleep(args.sleep)
            if existing_count(db, d) > 5000:  # 已填充则跳过，可续跑
                continue
            try:
                n = backfill_day(db, pro, d); total += n
            except Exception as exc:  # noqa: BLE001
                db.rollback(); print(f"{d} FAILED {str(exc)[:120]}", flush=True)
            if (i + 1) % 10 == 0 or i == len(dates) - 1:
                print(f"{i+1}/{len(dates)} {d} cum={total} { (time.time()-t0)/(i+1):.1f}s/d", flush=True)
        print(f"done auction rows={total} days={len(dates)}", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
