"""
参数网格扫描 — 陈小群龙头战法

扫描 _MIN_LIMIT_UP_FOR_FERMENT(3~10)、_MIN_PROMOTION_RATE(0.10~0.30)、_MIN_LIMIT_UP_FOR_CLIMAX(60~100)
共 12 组组合，每组跑 3 个月回测 (2026-03-01 ~ 2026-05-25)，输出最优组合。

用法:
  cd backend && source .venv/bin/activate && python scripts/param_scan_cxsq.py
"""

from __future__ import annotations

import logging
import sys
import time

sys.path.insert(0, ".")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# 12 组参数组合
# (名称, _MIN_LIMIT_UP_FOR_FERMENT, _MIN_PROMOTION_RATE_FOR_FERMENT, _MIN_LIMIT_UP_FOR_CLIMAX)
PARAM_GRID = [
    ("fer3_pr10_cli60",  3, 0.10, 60),
    ("fer3_pr18_cli85",  3, 0.18, 85),
    ("fer5_pr10_cli60",  5, 0.10, 60),
    ("fer5_pr18_cli85",  5, 0.18, 85),
    ("fer5_pr25_cli100", 5, 0.25, 100),
    ("fer8_pr10_cli60",  8, 0.10, 60),
    ("fer8_pr18_cli85",  8, 0.18, 85),
    ("fer8_pr25_cli100", 8, 0.25, 100),
    ("fer8_pr30_cli100", 8, 0.30, 100),
    ("fer10_pr18_cli85", 10, 0.18, 85),
    ("fer10_pr25_cli100", 10, 0.25, 100),
    ("fer3_pr25_cli60",  3, 0.25, 60),
]


def main() -> None:
    import app.services.chen_xiaoqun_service as cxsq
    import scripts.backtest_chen_xiaoqun as bt
    from app.db.session import SessionLocal

    all_results = []

    for name, ferment, promo, climax in PARAM_GRID:
        # 覆盖服务层参数
        cxsq._MIN_LIMIT_UP_FOR_FERMENT = ferment
        cxsq._MIN_PROMOTION_RATE_FOR_FERMENT = promo
        cxsq._MIN_LIMIT_UP_FOR_CLIMAX = climax

        # 覆盖回测日期
        bt.START_DATE = "2026-03-01"
        bt.END_DATE = "2026-05-25"

        logger.info("=" * 60)
        logger.info("参数组合: %s", name)
        logger.info("  _MIN_LIMIT_UP_FOR_FERMENT=%d, _MIN_PROMOTION_RATE=%.2f, _MIN_LIMIT_UP_FOR_CLIMAX=%d",
                    ferment, promo, climax)

        db = SessionLocal()
        t0 = time.perf_counter()
        try:
            metrics = bt._run_backtest(db)
        finally:
            db.close()
        elapsed = time.perf_counter() - t0

        metrics["name"] = name
        metrics["params"] = {"ferment": ferment, "promo": promo, "climax": climax}
        metrics["elapsed"] = round(elapsed, 1)
        all_results.append(metrics)

        logger.info("  耗时 %.1fs → 交易 %d 笔 胜率 %.1f%% 累计 %.1f%% 最大回撤 %.1f%%",
                    elapsed,
                    metrics.get("total_trades", 0),
                    metrics.get("accuracy", 0),
                    metrics.get("cumulative_return", 0),
                    metrics.get("max_dd", 0))

    # 按累计收益降序排列
    sorted_results = sorted(all_results, key=lambda x: x.get("cumulative_return", -999), reverse=True)

    print("\n" + "=" * 90)
    print("参数网格扫描结果 (2026-03-01 ~ 2026-05-25)")
    print("=" * 90)
    header = f"{'组合':<20} {'交易':>5} {'胜率':>7} {'平均收益':>8} {'盈亏比':>7} {'累计收益':>8} {'最大回撤':>8} {'耗时':>5}"
    print(header)
    print("-" * 90)
    for r in sorted_results:
        print(f"{r['name']:<20} {r.get('total_trades', 0):>5} {r.get('accuracy', 0):>6.1f}% "
              f"{r.get('average_net_return', 0):>7.2f}% {r.get('profit_loss_ratio', 0):>6.2f} "
              f"{r.get('cumulative_return', 0):>7.2f}% {r.get('max_dd', 0):>7.2f}% "
              f"{r.get('elapsed', 0):>4.0f}s")

    print("\n--- Top 3 ---")
    for i, r in enumerate(sorted_results[:3]):
        print(f"  #{i + 1}: {r['name']} (累计 {r.get('cumulative_return', 0):.1f}%, "
              f"胜率 {r.get('accuracy', 0):.1f}%, 回撤 {r.get('max_dd', 0):.1f}%)")

    best = sorted_results[0]
    print(f"\n最佳组合: {best['name']}")
    print(f"  _MIN_LIMIT_UP_FOR_FERMENT = {best['params']['ferment']}")
    print(f"  _MIN_PROMOTION_RATE       = {best['params']['promo']}")
    print(f"  _MIN_LIMIT_UP_FOR_CLIMAX  = {best['params']['climax']}")
    print(f"  累计收益: {best['cumulative_return']:.2f}%")
    print(f"  胜率:    {best['accuracy']:.1f}%")
    print(f"  最大回撤: {best['max_dd']:.2f}%")


if __name__ == "__main__":
    main()
