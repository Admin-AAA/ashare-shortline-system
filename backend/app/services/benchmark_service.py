"""基准对比服务：沪深300指数日线查询与收益计算。

供 backtest_service / outcome_service 共享使用，避免重复查询逻辑。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import IndexDaily

DEFAULT_BENCHMARK_TS_CODE = "000300.SH"
"""默认基准指数：沪深300。"""


def get_benchmark_close_map(
    db: Session,
    dates: list[str],
    ts_code: str = DEFAULT_BENCHMARK_TS_CODE,
) -> dict[str, float]:
    """批量查询基准指数在指定日期的收盘价。

    Parameters
    ----------
    dates : list[str]
        日期列表 (YYYY-MM-DD)。
    ts_code : str
        指数代码，默认沪深300。

    Returns
    -------
    dict[str, float]  {trade_date: close}，缺数据的日期不在 dict 中。
    """
    if not dates:
        return {}
    rows = db.execute(
        select(IndexDaily.trade_date, IndexDaily.close)
        .where(
            IndexDaily.ts_code == ts_code,
            IndexDaily.trade_date.in_(dates),
        )
    ).all()
    return {str(row.trade_date): float(row.close) for row in rows}


def compute_benchmark_return(
    close_map: dict[str, float],
    entry_date: str,
    exit_date: str,
) -> float:
    """计算基准指数在 [entry_date, exit_date] 期间收益率。

    若缺 entry 或 exit 的收盘价，取最接近的有效日期代替；
    若完全无法计算，返回 0.0。
    """
    entry_close = close_map.get(entry_date)
    exit_close = close_map.get(exit_date)

    if entry_close is None or exit_close is None or entry_close <= 0:
        return 0.0

    return round((exit_close - entry_close) / entry_close * 100, 3)


def seed_index_data(
    db: Session,
    *,
    lookback_days: int = 1095,
) -> dict[str, int]:
    """快速补全 index_daily 表（增量写入）。

    供启动初始化或数据修复用。
    Returns {code: written_count}
    """
    from datetime import date, timedelta

    from app.services.tushare_client import fetch_index_daily

    targets = ["000300.SH", "000905.SH"]
    end = date.today()
    start = end - timedelta(days=lookback_days)
    counts: dict[str, Any] = {}

    for ts_code in targets:
        rows = fetch_index_daily(
            ts_code=ts_code,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        if not rows:
            counts[ts_code] = 0
            continue
        written = 0
        for r in rows:
            existing = db.execute(
                select(IndexDaily).where(
                    IndexDaily.ts_code == r["ts_code"],
                    IndexDaily.trade_date == r["trade_date"],
                )
            ).scalar_one_or_none()
            if existing:
                continue
            db.add(IndexDaily(
                ts_code=r["ts_code"],
                trade_date=r["trade_date"],
                close=r["close"],
                pct_chg=r["pct_chg"],
                open=r["open"],
                high=r["high"],
                low=r["low"],
                volume=r["volume"],
            ))
            written += 1
        db.commit()
        counts[ts_code] = written
    return counts
