"""耐心版集合竞价回填：多轮重试 + 熔断冷却。

为什么需要：`stk_auction` 实际只有 promax 一个可用源（官方源已删除）
，而 promax 间歇 `http_server` 失败。
传输层的熔断是「3 次失败 → 打开 60s → half-open 放一个探针」，在紧凑循环里
会不断自我重开（每轮都撞熔断又记一次失败），于是单次运行跑到某天之后
**永远 circuit_open**（实测 2025-06-19 起连续 300 天全失败）。

本脚本把策略改成：整轮扫一遍缺失日 → 有进展就立刻下一轮 → 整轮零进展才睡
cooldown；这样熔断的 60s 窗口一定会被等到。

用法：
  cd backend && .venv/bin/python scripts/backfill_auction_patient.py \
      --start 2025-01-15 --end 2026-09-13 --passes 8 --cooldown 75
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from app.db.session import SessionLocal  # noqa: E402
from app.services.tushare_client import get_pro  # noqa: E402
from scripts.backfill_auction_direct import (  # noqa: E402
    backfill_day,
    existing_count,
    trade_dates,
)

MIN_ROWS = 5000  # 与 direct 版一致的"已填充"判据


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--passes", type=int, default=8)
    ap.add_argument("--cooldown", type=float, default=75.0)
    ap.add_argument("--sleep", type=float, default=1.0)
    args = ap.parse_args()

    db = SessionLocal()
    pro = get_pro()
    dates = trade_dates(db, args.start, args.end)
    print(f"[patient] 交易日 {len(dates)}，目标：每日 >{MIN_ROWS} 行", flush=True)
    t0 = time.time()
    try:
        for p in range(1, args.passes + 1):
            missing = [d for d in dates if existing_count(db, d) <= MIN_ROWS]
            if not missing:
                print(f"[patient] 全部完成，共 {len(dates)} 天", flush=True)
                break
            got = 0
            for i, d in enumerate(missing, 1):
                try:
                    n = backfill_day(db, pro, d)
                    if n > 0:
                        got += 1
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    if i <= 3 or i % 25 == 0:
                        print(f"  [{p}] {d} FAIL {str(exc)[:80]}", flush=True)
                time.sleep(args.sleep)
            done = len(dates) - len(missing) + got
            print(f"[pass {p}] 本轮补上 {got} 天；累计覆盖 {done}/{len(dates)} "
                  f"耗时 {(time.time()-t0)/60:.1f}min", flush=True)
            if got == 0:
                print(f"[pass {p}] 零进展 → 冷却 {args.cooldown}s（等熔断窗口过去）", flush=True)
                time.sleep(args.cooldown)
        left = [d for d in dates if existing_count(db, d) <= MIN_ROWS]
        print(f"[patient] 结束：仍缺 {len(left)} 天 {left[:10]}", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
