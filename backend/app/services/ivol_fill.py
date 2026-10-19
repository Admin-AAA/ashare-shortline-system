"""Incremental ivol20 / ivol_dn20 fill for daily_kbars (L-093 / L-109 risk vetoes).

Same basis as risk_features.compute_ivol20 / compute_ivol_dn20 (and scripts/backfill_ivol20.py).
Only rows whose value actually changes are written (and get updated_at bumped), so a re-run
never touches unchanged bars and never re-triggers outcome re-projection needlessly.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from sqlalchemy import text

logger = logging.getLogger(__name__)

IVOL_COVERAGE_ALERT_THRESHOLD = 0.80
_BJ_FILTER = "AND code NOT LIKE '8%' AND code NOT LIKE '4%' AND code NOT LIKE '920%'"


def _builders():
    from app.services.risk_features import build_ivol_dn20_panel, build_ivol_panel

    return {"ivol20": build_ivol_panel, "ivol_dn20": build_ivol_dn20_panel}


def fill_ivol_range(db, start: str, end: str, metric: str = "ivol20") -> int:
    """Compute `metric` for trade_date in [start, end] (120 calendar-day warmup) and write changes."""
    from app.services.risk_features import BENCHMARK_TS_CODE

    builder = _builders()[metric]
    col = metric
    warm = (pd.Timestamp(start) - pd.Timedelta(days=120)).strftime("%Y-%m-%d")
    kbars = pd.DataFrame(db.execute(text(
        "SELECT code, trade_date, pct_chg FROM daily_kbars "
        f"WHERE trade_date >= :w AND trade_date <= :b {_BJ_FILTER} ORDER BY code, trade_date"),
        {"w": warm, "b": end}).mappings().all())
    if kbars.empty:
        return 0
    idx = pd.DataFrame(db.execute(text(
        "SELECT trade_date, pct_chg FROM index_daily WHERE ts_code=:c AND trade_date>=:w AND trade_date<=:b "
        "ORDER BY trade_date"), {"c": BENCHMARK_TS_CODE, "w": warm, "b": end}).mappings().all())
    if idx.empty:
        logger.warning("ivol fill: benchmark %s index_daily empty for %s..%s", BENCHMARK_TS_CODE, warm, end)
        return 0
    panel = builder(kbars, idx)
    panel = panel[(panel.trade_date >= start) & (panel.trade_date <= end)].dropna(subset=[col])
    if panel.empty:
        return 0
    panel = panel[["code", "trade_date", col]].copy()
    panel[col] = panel[col].astype(float).round(6)
    conn = db.connection()
    conn.execute(text("DROP TEMPORARY TABLE IF EXISTS _ivol_tmp"))
    conn.execute(text(f"CREATE TEMPORARY TABLE _ivol_tmp (code VARCHAR(6), trade_date VARCHAR(10), {col} DOUBLE, "
                      "PRIMARY KEY(code, trade_date))"))
    panel.to_sql("_ivol_tmp", con=conn, if_exists="append", index=False, chunksize=20000)
    res = conn.execute(text(
        f"UPDATE daily_kbars k JOIN _ivol_tmp t ON k.code=t.code AND k.trade_date=t.trade_date "
        f"SET k.{col}=t.{col}, k.updated_at=UTC_TIMESTAMP() "
        f"WHERE NOT (ROUND(k.{col}, 6) <=> t.{col})"))
    conn.execute(text("DROP TEMPORARY TABLE IF EXISTS _ivol_tmp"))
    db.commit()
    return int(res.rowcount or 0)


def ivol_coverage(db, trade_date: str, metric: str = "ivol20") -> float:
    row = db.execute(text(
        f"SELECT COUNT(*), SUM({metric} IS NOT NULL) FROM daily_kbars WHERE trade_date=:d {_BJ_FILTER}"),
        {"d": trade_date}).one()
    total, filled = int(row[0] or 0), int(row[1] or 0)
    return (filled / total) if total else 0.0


def check_ivol_coverage(db, trade_date: str, metric: str = "ivol20") -> dict[str, Any]:
    from app.core.alerts import emit_alert

    cov = ivol_coverage(db, trade_date, metric)
    ok = cov >= IVOL_COVERAGE_ALERT_THRESHOLD
    if not ok:
        emit_alert(
            "ivol_coverage",
            f"{metric}:{trade_date}",
            f"{metric} 覆盖率 {cov:.1%} < {IVOL_COVERAGE_ALERT_THRESHOLD:.0%}（{trade_date}），"
            "L-093/L-109/L-121 风控否决将缺数据",
        )
    return {"trade_date": trade_date, "metric": metric, "coverage": round(cov, 4), "ok": ok}


def fill_ivol_incremental(db, trade_date: str, lookback_days: int = 10) -> dict[str, Any]:
    """Post-close: (re)compute ivol20 + ivol_dn20 for the last `lookback_days` calendar days, then check coverage."""
    start = (pd.Timestamp(trade_date) - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    out: dict[str, Any] = {"start": start, "end": trade_date}
    for metric in ("ivol20", "ivol_dn20"):
        out[metric] = {"updated": fill_ivol_range(db, start, trade_date, metric)}
        out[metric]["coverage"] = check_ivol_coverage(db, trade_date, metric)
    return out
