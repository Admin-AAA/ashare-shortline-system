"""按交易日回填历史全市场 日K / daily_basic / 资金流（Tushare，一日 3 次调用，限速、可续跑、避开日更窗口）。

为 2–3 年多市况回测补数据（sprint2）。只写 OHLCV/成交额/涨跌幅/换手（daily+daily_basic），
指标列不在这里写——跑完后用 scripts/backfill_indicators.py --start/--end 本地算（MA/MACD/RSI/KDJ/BOLL 等）。
筹码（cyq_perf）、stk_factor 扩展因子不回填（promax 限流且官方无权限），这些特征在历史段走 FALLBACK。
资金流累计列（3/5/10 日）与 main_net_inflow_ratio 留空，避免依赖窗口外数据。

用法：python scripts/backfill_history_fullmarket.py --start 2022-01-04 --end 2024-12-31 [--sleep 1.5] [--limit-days N]
"""
from __future__ import annotations
import argparse, sys, time
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.tushare_client import get_pro
from app.services.data_gap_remediation_service import _daily_kbar_payload, _upsert_daily_kbar_payload
from scripts.backfill_daily_basic import backfill as backfill_basic

# 生产日更（17:35 post_sync，约 20 分钟；21:00 兜底补数）期间暂停，避免抢 Tushare 配额。
QUIET = ((17, 0, 19, 45), (20, 50, 21, 45))

def _quiet_wait():
    while True:
        now = datetime.now(); m = now.hour * 60 + now.minute
        if not any(a * 60 + b <= m < c * 60 + d for a, b, c, d in QUIET):
            return
        time.sleep(60)

def _f(v):
    try:
        v = float(v)
        return None if v != v else v
    except (TypeError, ValueError):
        return None

def _done(db, d):
    k = db.execute(text("select count(*) from daily_kbars where trade_date=:d"), {"d": d}).scalar() or 0
    b = db.execute(text("select count(*) from daily_basic where trade_date=:d"), {"d": d}).scalar() or 0
    c = db.execute(text("select count(*) from capital_flow where trade_date=:d"), {"d": d}).scalar() or 0
    return k > 3000 and b > 3000 and c > 3000

def day(db, pro, d):
    ds = d.replace("-", "")
    df = pro.daily(trade_date=ds)
    basic = pro.daily_basic(trade_date=ds, fields="ts_code,turnover_rate")
    tr = {} if basic is None else {str(r.ts_code).split(".")[0]: _f(r.turnover_rate) for r in basic.itertuples()}
    rows = []
    for r in (df.itertuples() if df is not None else []):
        code = str(r.ts_code).split(".")[0]
        if len(code) != 6 or not code.isdigit():
            continue
        rows.append(_daily_kbar_payload({"code": code, "trade_date": d, "open": r.open, "high": r.high, "low": r.low, "close": r.close,
                                         "volume": r.vol, "amount": (_f(r.amount) or 0) * 1000, "pct_chg": r.pct_chg, "turnover_rate": tr.get(code)}))
    if rows:
        _upsert_daily_kbar_payload(db, rows); db.commit()
    nb = backfill_basic(db, pro, d)
    mf = pro.moneyflow(trade_date=ds)
    nm = 0
    if mf is not None and len(mf):
        db.execute(text("delete from capital_flow where trade_date=:d"), {"d": d})
        vals = []
        for r in mf.itertuples():
            code = str(r.ts_code).split(".")[0]
            g = lambda k: _f(getattr(r, k, None)) or 0.0
            sl = round((g("buy_elg_amount") - g("sell_elg_amount")) / 1e4, 4); lg = round((g("buy_lg_amount") - g("sell_lg_amount")) / 1e4, 4)
            # 与库内既有口径一致：主力 = 超大单 + 大单净额（亿元）；ratio 口径不同源，留空。
            vals.append({"code": code, "trade_date": d, "m": round(sl + lg, 4),
                         "s": round((g("buy_elg_amount") - g("sell_elg_amount")) / 1e4, 4),
                         "l": round((g("buy_lg_amount") - g("sell_lg_amount")) / 1e4, 4),
                         "md": round((g("buy_md_amount") - g("sell_md_amount")) / 1e4, 4),
                         "sm": round((g("buy_sm_amount") - g("sell_sm_amount")) / 1e4, 4), "r": None})
        db.execute(text("insert into capital_flow (code,trade_date,main_net_inflow_yi,super_large_net_inflow_yi,large_net_inflow_yi,"
                        "medium_net_inflow_yi,small_net_inflow_yi,main_net_inflow_ratio,created_at) values "
                        "(:code,:trade_date,:m,:s,:l,:md,:sm,:r,now())"), vals)
        db.commit(); nm = len(vals)
    return len(rows), nb, nm

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--start", required=True); ap.add_argument("--end", required=True)
    ap.add_argument("--sleep", type=float, default=1.5); ap.add_argument("--limit-days", type=int, default=0)
    a = ap.parse_args(); pro = get_pro(); db = SessionLocal()
    cal = pro.trade_cal(exchange="SSE", start_date=a.start.replace("-", ""), end_date=a.end.replace("-", ""), is_open="1")
    dates = sorted(f"{s[:4]}-{s[4:6]}-{s[6:]}" for s in cal["cal_date"].astype(str))
    print(f"{len(dates)} trade dates {dates[0]}..{dates[-1]}", flush=True)
    n = 0
    for d in dates:
        if _done(db, d):
            continue
        _quiet_wait(); t = time.time()
        for attempt in range(3):
            try:
                k, b, m = day(db, pro, d); break
            except Exception as e:
                db.rollback(); print(d, "error", attempt, repr(e)[:200], flush=True); time.sleep(30 * (attempt + 1))
        else:
            continue
        print(d, "kbar", k, "basic", b, "flow", m, f"{time.time() - t:.1f}s", flush=True)
        n += 1
        if a.limit_days and n >= a.limit_days:
            break
        time.sleep(a.sleep)

if __name__ == "__main__":
    main()
