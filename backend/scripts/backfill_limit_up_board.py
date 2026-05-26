"""
回填 limit_up_board 历史涨停数据。

从 Tushare limit_list_d 接口按月分批获取 2024-01-01 ~ 2026-03-22 数据，
写入 limit_up_board 表（ON DUPLICATE KEY UPDATE 防重）。

用法：
  cd backend && source .venv/bin/activate && python scripts/backfill_limit_up_board.py
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.models.tables import LimitUpBoard
from scripts.tushare_init import get_pro

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_limit_up")

BATCH_SIZE = 500
START_DATE = "20240101"
END_DATE = "20260322"

DB_URL = "mysql+pymysql://aowang:1279990918@139.196.242.53:4407/ashare_workbench?charset=utf8mb4"


def _f(val):
    try:
        import math
        v = float(val)
        return 0.0 if math.isnan(v) else v
    except (TypeError, ValueError):
        return 0.0


def _safe_int(val, default=0):
    """安全转换 int，处理 NaN 和 None。"""
    if val is None:
        return default
    try:
        import math
        if isinstance(val, float) and math.isnan(val):
            return default
        return int(val)
    except (TypeError, ValueError):
        return default


def _to_date_str(raw: str) -> str:
    """将 YYYYMMDD 转为 YYYY-MM-DD。"""
    raw = raw.strip()
    if len(raw) == 8:
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def _month_ranges(start: str, end: str):
    """生成 (YYYYMMDD_start, YYYYMMDD_end) 月份区间列表。"""
    from datetime import date, timedelta

    s = date(int(start[:4]), int(start[4:6]), int(start[6:]))
    e = date(int(end[:4]), int(end[4:6]), int(end[6:]))

    ranges = []
    cur = s
    while cur <= e:
        # 当月最后一天
        if cur.month == 12:
            last = cur.replace(day=31)
        else:
            last = date(cur.year, cur.month + 1, 1) - timedelta(days=1)
        month_end = min(last, e)
        ranges.append((cur.strftime("%Y%m%d"), month_end.strftime("%Y%m%d")))
        cur = date(cur.year + (cur.month // 12), (cur.month % 12) + 1, 1)
    return ranges


def _map_row(row) -> dict | None:
    """将 Tushare DataFrame 一行映射为入库 dict。"""
    try:
        trade_date = _to_date_str(str(row.get("trade_date", "")))
        code = str(row.get("ts_code", "")).split(".")[0]
        if not code or len(code) != 6:
            return None

        limit_amt = row.get("limit_amount")
        fd_amt = row.get("fd_amount")
        turnover = row.get("turnover_ratio")

        return {
            "code": code,
            "trade_date": trade_date,
            "name": str(row.get("name", "")),
            "pct_chg": _f(row.get("pct_chg")),
            "limit_type": str(row.get("limit", "")),
            "limit_times": _safe_int(row.get("limit_times"), 1),
            "first_time": str(row.get("first_time", "")),
            "last_time": str(row.get("last_time", "")),
            "open_times": _safe_int(row.get("open_times"), 0),
            "up_stat": str(row.get("up_stat", "")),
            "limit_amount_yi": _f(limit_amt) / 1e8 if limit_amt is not None else None,
            "turnover_ratio": _f(turnover) if turnover is not None else None,
            "fd_amount_yi": _f(fd_amt) / 1e8 if fd_amt is not None else None,
            "industry": str(row.get("industry", "")),
        }
    except Exception as e:
        logger.warning("跳过异常行: %s", e)
        return None


def main():
    pro = get_pro()
    engine = create_engine(DB_URL, connect_args={"charset": "utf8mb4"}, pool_pre_ping=True)

    ranges = _month_ranges(START_DATE, END_DATE)
    logger.info("按月分批查询: %d 个月份区间", len(ranges))

    total_all = 0

    for i, (ms, me) in enumerate(ranges):
        logger.info("[%d/%d] 查询 %s ~ %s ...", i + 1, len(ranges), ms, me)
        try:
            df = pro.limit_list_d(start_date=ms, end_date=me)
        except Exception as e:
            logger.warning("  %s ~ %s 请求失败: %s", ms, me, e)
            continue

        if df is None or df.empty:
            logger.info("  无数据")
            continue

        # 字段映射
        records = [r for r in (_map_row(row) for _, row in df.iterrows()) if r is not None]
        if not records:
            continue

        # 分批入库
        with engine.begin() as conn:
            for j in range(0, len(records), BATCH_SIZE):
                batch = records[j : j + BATCH_SIZE]
                stmt = mysql_insert(LimitUpBoard).values(batch)
                stmt = stmt.on_duplicate_key_update(id=LimitUpBoard.id)
                conn.execute(stmt)

        total_all += len(records)
        logger.info("  +%d 条 (累计 %d)", len(records), total_all)

    logger.info("全部完成，共入库 %d 条", total_all)

    # 验证
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT trade_date), COUNT(*) FROM limit_up_board")
        ).fetchone()
        if result:
            logger.info("验证: limit_up_board  %s ~ %s  |  %d 个交易日  |  %d 条记录",
                        result[0], result[1], result[2], result[3])

    engine.dispose()


if __name__ == "__main__":
    main()
