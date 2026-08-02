"""回填 stk_limits（每日涨跌停价）历史全窗口。

背景：stk_limits 表此前仅覆盖 2026-05-11 之后（56 个交易日），2024-01~2026-05
之间约 568 个交易日缺失；而 2025-07/09 回放窗口的涨跌停判定因此没有真实数据，
造成回测口径乐观偏差（见系统深度诊断报告 2026-08-02）。

策略：从 daily_kbars 取 2024-01-01 起的真实交易日清单，剔除 stk_limits 已覆盖
日期，逐日调用 pro.stk_limit(trade_date=)（一次拉全市场，约 7000 行/日），
mysql_insert + on_duplicate_key_update 幂等写入。失败日期重试 1 次后记录，
可断点续跑（已覆盖日期自动跳过）。

用法：
    backend/.venv/bin/python backend/scripts/backfill_stk_limits_history.py \
        [--start 2024-01-01] [--end 2026-07-31] [--sleep 0.3]
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert

BACKEND_ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(BACKEND_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_stk_limits")  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.models.tables import DailyKbar, StkLimit  # noqa: E402
from app.services.tushare_client import get_pro  # noqa: E402


def _compact(iso: str) -> str:
    return iso.replace("-", "")


def main() -> None:
    parser = argparse.ArgumentParser(description="回填 stk_limits 历史窗口")
    parser.add_argument("--start", default="2024-01-01", help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", default="2026-07-31", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--sleep", type=float, default=0.3, help="每次调用间隔秒")
    parser.add_argument("--dry-run", action="store_true", help="只列出缺口清单，不拉取")
    args = parser.parse_args()

    db = next(get_db())
    try:
        # 1. 交易日清单（以 daily_kbars 为准）
        trade_days = sorted(
            row[0]
            for row in db.execute(
                select(DailyKbar.trade_date)
                .where(DailyKbar.trade_date >= args.start, DailyKbar.trade_date <= args.end)
                .distinct()
            )
        )
        # 2. 已覆盖日期
        covered = {row[0] for row in db.execute(select(StkLimit.trade_date).distinct())}
        gaps = [d for d in trade_days if d not in covered]
        print(f"交易日总数={len(trade_days)} 已覆盖={len(trade_days) - len(gaps)} 缺口={len(gaps)}")
        if args.dry_run:
            print("缺口日期（前 20）：", gaps[:20])
            print("缺口日期（后 10）：", gaps[-10:])
            return

        pro = get_pro()
        stats = {"success": 0, "empty": 0, "failed": []}
        for i, iso in enumerate(gaps, 1):
            compact = _compact(iso)
            try:
                df = pro.stk_limit(trade_date=compact)
            except Exception as exc:  # 重试一次
                time.sleep(1.0)
                try:
                    df = pro.stk_limit(trade_date=compact)
                except Exception as exc2:
                    stats["failed"].append(f"{iso}: {type(exc2).__name__}: {str(exc2)[:100]}")
                    print(f"[{i}/{len(gaps)}] {iso} FAILED（重试后）")
                    continue
            if df is None or df.empty:
                stats["empty"] += 1
                print(f"[{i}/{len(gaps)}] {iso} empty")
                time.sleep(args.sleep)
                continue
            rows = []
            for _, r in df.iterrows():
                code = str(r.get("ts_code", "")).split(".")[0]
                if len(code) != 6:
                    continue
                rows.append(
                    {
                        "code": code,
                        "trade_date": iso,
                        "up_limit": _f(r.get("up_limit")),
                        "down_limit": _f(r.get("down_limit")),
                    }
                )
            if not rows:
                stats["empty"] += 1
                print(f"[{i}/{len(gaps)}] {iso} empty(rows=0)")
                time.sleep(args.sleep)
                continue
            stmt = mysql_insert(StkLimit).values(rows)
            stmt = stmt.on_duplicate_key_update(
                up_limit=stmt.inserted.up_limit,
                down_limit=stmt.inserted.down_limit,
            )
            db.execute(stmt)
            db.commit()
            stats["success"] += 1
            if i % 20 == 0 or i == len(gaps):
                print(f"[{i}/{len(gaps)}] {iso} ok rows={len(rows)} 累计成功={stats['success']}")
            time.sleep(args.sleep)

        print("\n==== 回填完成 ====")
        print(f"成功={stats['success']} 空={stats['empty']} 失败={len(stats['failed'])}")
        for f in stats["failed"][:20]:
            print("  FAILED:", f)
        # 3. 覆盖验证
        covered_after = {row[0] for row in db.execute(select(StkLimit.trade_date).distinct())}
        missing = [d for d in trade_days if d not in covered_after]
        print(f"覆盖验证：缺口剩 {len(missing)} 天")
        if missing:
            print("  仍缺失:", missing[:20])
    finally:
        db.close()


def _f(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
