"""按交易日重建「快照类」缺口：daily_basic + stock_quotes（人工修复工具）。

用途
----
机器离线导致盘后行情池刷新漏跑时，`daily_basic` / `stock_quotes` 会整日缺行，
formal 安全网随后连续失败。开机自愈**不会**自动补 stock_quotes——盘中实时快照
含无法从日K无损还原的时点字段，自动组装会造出「看起来像真的」数据、污染 PIT 证据。
本脚本把之前散落在 /tmp 的一次性修复固化成可复用工具，供告警指引人工执行。

诚实性约定
----------
写入的行固定标记 `source='backfill:daily_kbars'`、`freshness_status='stale'`、
`quality_score=0.9`，只用于补齐历史完整性 / 解除安全网，绝不伪装成当日实时快照，
也不得用于实盘出票。默认 `INSERT IGNORE` 幂等，不覆盖已有数据。

用法（cd backend）
------------------
  PYTHONPATH=. .venv/bin/python scripts/repair_snapshot_day.py --date 2026-11-13 --dry-run
  PYTHONPATH=. .venv/bin/python scripts/repair_snapshot_day.py --date 2026-11-13
  PYTHONPATH=. .venv/bin/python scripts/repair_snapshot_day.py --date 2026-11-13 --skip-basic

凭据一律走项目 `.env` / `app.db.session.SessionLocal`，脚本内不写任何明文密码。
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402

MIN_BASIC_ROWS = 3000


def num(v):
    """NaN / None -> None，其余 float。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _rows(db, table: str, day: str) -> int:
    return int(db.execute(text(
        f"SELECT COUNT(*) FROM {table} WHERE trade_date IN (:d, :dc)"),
        {"d": day, "dc": day.replace("-", "")}).scalar() or 0)


def backfill_daily_basic(db, day: str, compact: str, dry_run: bool) -> dict:
    """上游 pro.daily_basic 拉当日全市场，INSERT IGNORE 补齐。"""
    have = _rows(db, "daily_basic", day)
    if have >= MIN_BASIC_ROWS:
        return {"status": "skipped", "existing": have}
    from app.services.tushare_client import get_pro

    df = get_pro().daily_basic(trade_date=compact)
    if df is None or not len(df):
        return {"status": "failed", "reason": "daily_basic 上游返回空"}
    rows = []
    for _, r in df.iterrows():
        ts_code = str(r.get("ts_code") or "")
        if not ts_code:
            continue
        rows.append({
            "c": ts_code.split(".")[0], "d": day,
            "t": num(r.get("turnover_rate")) or 0,
            "pe": num(r.get("pe_ttm")), "mv": num(r.get("total_mv")), "cmv": num(r.get("circ_mv")),
        })
    if dry_run:
        return {"status": "dry_run", "existing": have, "would_insert": len(rows)}
    res = db.execute(text(
        "INSERT IGNORE INTO daily_basic (code, trade_date, turnover_rate, pe_ttm, total_mv, circ_mv) "
        "VALUES (:c, :d, :t, :pe, :mv, :cmv)"), rows)
    db.commit()
    return {"status": "completed", "existing": have, "inserted": res.rowcount, "after": _rows(db, "daily_basic", day)}


def backfill_stock_quotes(db, day: str, dry_run: bool) -> dict:
    """由 daily_kbars + daily_basic + stocks 组装收盘快照，显式标记为 stale 回填。"""
    have = _rows(db, "stock_quotes", day)
    if have >= MIN_BASIC_ROWS:
        return {"status": "skipped", "existing": have}
    bars = db.execute(text(
        "SELECT code, open, high, low, close, volume, amount, pct_chg FROM daily_kbars "
        "WHERE trade_date IN (:d, :dc) AND close>0"),
        {"d": day, "dc": day.replace("-", "")}).mappings().all()
    basics = {r["code"]: r for r in db.execute(text(
        "SELECT code, turnover_rate, pe_ttm, total_mv, circ_mv FROM daily_basic "
        "WHERE trade_date IN (:d, :dc)"), {"d": day, "dc": day.replace("-", "")}).mappings().all()}
    stocks = {r["code"]: r for r in db.execute(text(
        "SELECT code, name, industry FROM stocks")).mappings().all()}
    if not bars:
        return {"status": "failed", "reason": "daily_kbars 当日无数据，无法组装快照"}

    rows = []
    for b in bars:
        code = b["code"]
        st = stocks.get(code) or {}
        bs = basics.get(code) or {}
        amount_yi = 0.0 if b["amount"] is None else float(b["amount"]) / 1e8
        mv, cmv = num(bs.get("total_mv")), num(bs.get("circ_mv"))
        close, pct = float(b["close"]), float(b["pct_chg"] or 0)
        rows.append({
            "code": code, "d": day, "name": st.get("name") or "",
            "price": close, "pct": pct,
            "volume": float(b["volume"] or 0), "amount_yi": round(amount_yi, 4),
            "turnover_rate": num(bs.get("turnover_rate")) or 0,
            "pe_ttm": num(bs.get("pe_ttm")),
            "total_mv_yi": None if mv is None else round(mv / 1e4, 2),
            "free_mv_yi": None if cmv is None else round(cmv / 1e4, 2),
            "high": float(b["high"] or 0), "low": float(b["low"] or 0), "open": float(b["open"] or 0),
            "preclose": round(close / (1 + pct / 100), 2) if pct != -100 else close,
            "industry": st.get("industry") or "",
        })
    if dry_run:
        return {"status": "dry_run", "existing": have, "would_insert": len(rows),
                "bars": len(bars), "with_basic": sum(1 for r in rows if r["pe_ttm"] is not None)}
    res = db.execute(text(
        "INSERT IGNORE INTO stock_quotes (code, trade_date, name, price, pct, volume, amount_yi, "
        "turnover_rate, pe_ttm, total_mv_yi, free_mv_yi, chg60, high, low, open, preclose, industry, "
        "source, source_count, quality_score, freshness_status, snapped_at) "
        "VALUES (:code, :d, :name, :price, :pct, :volume, :amount_yi, :turnover_rate, :pe_ttm, "
        ":total_mv_yi, :free_mv_yi, NULL, :high, :low, :open, :preclose, :industry, "
        "'backfill:daily_kbars', 1, 0.9, 'stale', NOW())"), rows)
    db.commit()
    return {"status": "completed", "existing": have, "inserted": res.rowcount,
            "after": _rows(db, "stock_quotes", day)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True, help="目标交易日 YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库（仍会拉上游 daily_basic）")
    ap.add_argument("--skip-basic", action="store_true", help="跳过 daily_basic 补齐（已有该日数据时用）")
    a = ap.parse_args()
    try:
        day = dt.date.fromisoformat(a.date).isoformat()
    except ValueError:
        print("ERROR: --date 必须是 YYYY-MM-DD", file=sys.stderr)
        return 2
    compact = day.replace("-", "")

    print(f"目标交易日 {day} dry_run={a.dry_run}")
    print("注意：stock_quotes 将标记为 source=backfill:daily_kbars / freshness_status=stale，不得用于实盘出票")
    with SessionLocal() as db:
        out: dict = {"date": day}
        if not a.skip_basic:
            try:
                out["daily_basic"] = backfill_daily_basic(db, day, compact, a.dry_run)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                out["daily_basic"] = {"status": "failed", "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
        try:
            out["stock_quotes"] = backfill_stock_quotes(db, day, a.dry_run)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            out["stock_quotes"] = {"status": "failed", "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
        out["verify"] = {"daily_basic": _rows(db, "daily_basic", day),
                         "stock_quotes": _rows(db, "stock_quotes", day)} if not a.dry_run else None
    import json

    print(json.dumps(out, ensure_ascii=False, indent=2))
    failed = [k for k, v in out.items() if isinstance(v, dict) and v.get("status") == "failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
