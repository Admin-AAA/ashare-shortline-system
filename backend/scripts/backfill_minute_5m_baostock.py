"""用 Baostock 按股票范围模式回填 5min 分钟K（覆盖指定历史窗口）。

与 ``_backfill_minute_kbar_date``（按日逐股）不同：Baostock
``query_history_k_data_plus`` 单次查询即可返回该股 [start,end] 整段历史
（实测 5min 半年 117 交易日 ≈ 5616 行 / 4.2s），故按股一次拉整段最经济。
全市场 5569 股 × ~4.2s ≈ 6.5 小时，务必后台跑。

Tushare ``stk_mins`` 无权限（2026-08 实测），Baostock 支持 5/15/30/60min。
底层 ``_fetch_baostock_minute_kbars`` 已全局串行 + >=3.5s 限速 + 失败重 login。

幂等：按唯一键 (code, freq, trade_time) ON DUPLICATE KEY UPDATE；支持
``--resume`` 跳过已完成（窗口内已有数据的）股票。

用法：
    PYTHONPATH=backend backend/.venv/bin/python \
        backend/scripts/backfill_minute_5m_baostock.py \
        --start-date 2026-01-30 --end-date 2026-07-28 --freq 5min
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.db.session import SessionLocal
from app.models.tables import DailyKbar, MinuteKbar
from app.services.tushare_client import _fetch_baostock_minute_kbars

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_minute_5m")


def _active_codes(db, start_date: str) -> list[str]:
    """窗口内有日K的活跃股票（全市场宽召回口径）。"""
    rows = db.execute(
        select(DailyKbar.code).where(DailyKbar.trade_date >= start_date).distinct()
    ).all()
    return sorted(str(r[0]) for r in rows if r and r[0])


def _done_codes(db, freq: str, start_date: str, end_date: str) -> set[str]:
    """窗口内已有该 freq 数据的股票（resume 跳过）。"""
    rows = db.execute(
        select(MinuteKbar.code)
        .where(
            MinuteKbar.freq == freq,
            MinuteKbar.trade_date >= start_date,
            MinuteKbar.trade_date <= end_date,
        )
        .group_by(MinuteKbar.code)
        .having(func.count(MinuteKbar.id) > 0)
    ).all()
    return {str(r[0]) for r in rows if r and r[0]}


def _upsert_rows(db, rows: list[dict]) -> int:
    payload = [
        {
            "code": r["code"],
            "freq": r["freq"],
            "trade_date": r["trade_date"],
            "trade_time": r["trade_time"],
            "open": r.get("open"),
            "high": r.get("high"),
            "low": r.get("low"),
            "close": r.get("close"),
            "volume": r.get("volume"),
            "amount": r.get("amount"),
        }
        for r in rows
        if r.get("trade_date") and r.get("trade_time")
    ]
    if not payload:
        return 0
    stmt = mysql_insert(MinuteKbar).values(payload)
    db.execute(
        stmt.on_duplicate_key_update(
            open=stmt.inserted.open,
            high=stmt.inserted.high,
            low=stmt.inserted.low,
            close=stmt.inserted.close,
            volume=stmt.inserted.volume,
            amount=stmt.inserted.amount,
        )
    )
    db.commit()
    return len(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Baostock 5min 分钟K范围回填")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--freq", default="5min")
    parser.add_argument("--limit", type=int, default=None, help="调试用：只处理前 N 股")
    parser.add_argument("--resume", action="store_true", help="跳过窗口内已有数据的股票")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        codes = _active_codes(db, args.start_date)
        if args.resume:
            done = _done_codes(db, args.freq, args.start_date, args.end_date)
            codes = [c for c in codes if c not in done]
            logger.info("resume: 跳过已有数据 %d 股，剩余 %d 股", len(done), len(codes))
        if args.limit:
            codes = codes[: args.limit]
        total = len(codes)
        logger.info(
            "回填开始: freq=%s 窗口=%s~%s 股票=%d", args.freq, args.start_date, args.end_date, total
        )
        t0 = time.time()
        stored_total = 0
        failed: list[str] = []
        for idx, code in enumerate(codes, 1):
            try:
                rows = _fetch_baostock_minute_kbars(code, args.freq, args.start_date, args.end_date)
                for r in rows:
                    r["freq"] = args.freq
                stored = _upsert_rows(db, rows)
                stored_total += stored
                if not rows:
                    failed.append(code)
            except Exception as exc:  # 单股失败不中断整体
                db.rollback()
                failed.append(code)
                logger.warning("回填失败 code=%s: %s", code, str(exc)[:120])
            if idx % 100 == 0 or idx == total:
                elapsed = time.time() - t0
                rate = idx / elapsed if elapsed else 0
                eta = (total - idx) / rate if rate else 0
                logger.info(
                    "进度 %d/%d 已存 %d 行 失败 %d 股 用时 %.0fs ETA %.0f 分",
                    idx, total, stored_total, len(failed), elapsed, eta / 60,
                )
        logger.info(
            "回填完成: 股票=%d 共存 %d 行 失败/空 %d 股 总用时 %.1f 分",
            total, stored_total, len(failed), (time.time() - t0) / 60,
        )
        if failed:
            logger.info("失败/空股票样例: %s", failed[:20])
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
