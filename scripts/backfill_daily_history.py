#!/usr/bin/env python3
"""按交易日批量回填全市场日线历史（补齐早期数据深度缺口）。

背景：本地 daily_kbars 自 2025-05 才全量回填，2025-01~10 早期每天仅 297~3762 只，
导致 P0 回测的 95% 覆盖率门槛（60 根回看）达不到，整批 insufficient_data。
本脚本对指定区间每个真实交易日调 pro.daily(trade_date=) 拉全市场（5500+ 只），
幂等 upsert 补齐缺失股票，使回测能在全市场口径下跑通。

交易日来源：pro.trade_cal(exchange='SSE', is_open='1') —— 不依赖 daily_kbars
已存在的 trade_date（否则早期完全缺失的交易日永远不会被回填，陷入"没数据就不补"死循环）。

用法：
  python scripts/backfill_daily_history.py --start 2025-01-02 --end 2025-10-24
  python scripts/backfill_daily_history.py --months 6

约束（CLAUDE.md）：批量优先（pro.daily(trade_date=) 一次拉全市场）；
落库 on_duplicate_key_update 幂等可重跑；只在主仓 backend 执行（需 .env Tushare token）。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.db.session import SessionLocal  # noqa: E402
from app.services.data_gap_remediation_service import _daily_kbar_payload, _upsert_daily_kbar_payload  # noqa: E402
from app.services.tushare_client import fetch_daily_kbars_for_trade_date, get_pro  # noqa: E402


def fetch_trade_calendar(start: str, end: str) -> list[str]:
    """从 pro.trade_cal 取区间内真实交易日（YYYYMMDD），不依赖本地 daily_kbars。

    旧版从 daily_kbars 已有 trade_date 取，导致早期完全缺失的交易日永远不被回填。
    """
    s = start.replace("-", "")
    e = end.replace("-", "")
    pro = get_pro()
    cal = pro.trade_cal(exchange="SSE", start_date=s, end_date=e, is_open="1")
    if cal is None or cal.empty:
        return []
    return sorted(str(d) for d in cal["cal_date"].tolist())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=6, help="回填最近 N 个月信号期（默认 6；补早期缺口请用 --start 显式指定）")
    ap.add_argument("--start", help="显式起始日 YYYY-MM-DD（覆盖 --months；补早期缺口时必填，如 2025-01-02）")
    ap.add_argument("--end", help="显式结束日 YYYY-MM-DD")
    args = ap.parse_args()

    today = date.today()
    end_date = (args.end or today.isoformat())
    if args.start:
        start_date = args.start
    else:
        # 信号期 N 个月 + 60 根回看缓冲（约 3 个自然月）
        buf = today - timedelta(days=args.months * 30 + 95)
        start_date = buf.isoformat()

    cal = fetch_trade_calendar(start_date, end_date)
    print(f"回填区间 {start_date} ~ {end_date}：{len(cal)} 个真实交易日（来源 pro.trade_cal）")
    if not cal:
        print("无交易日可回填，退出"); return 0

    db = SessionLocal()
    try:
        total = len(cal)
        ok = skip = fail = 0
        t0 = time.time()
        for i, td in enumerate(cal, 1):
            try:
                bars = fetch_daily_kbars_for_trade_date(td)
                if not bars:
                    print(f"  [{i}/{total}] {td}: 上游空（非交易日/停盘），跳过")
                    skip += 1; continue
                payload = [_daily_kbar_payload(b) for b in bars]
                _upsert_daily_kbar_payload(db, payload)
                ok += 1
                if i % 10 == 0 or i == total:
                    el = time.time() - t0
                    print(f"  [{i}/{total}] {td}: upsert {len(payload)} 行 | 累计 ok={ok} skip={skip} fail={fail} {el:.0f}s")
            except Exception as exc:  # noqa: BLE001 - 单日失败不中断
                db.rollback()
                fail += 1
                print(f"  [{i}/{total}] {td}: FAIL {str(exc)[:100]}")
        print(f"\n完成：ok={ok} skip={skip} fail={fail}  总耗时 {time.time()-t0:.0f}s")
        return 0 if fail == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
