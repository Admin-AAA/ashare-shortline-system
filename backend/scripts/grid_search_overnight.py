"""
隔夜持股参数网格扫描脚本。

扫描参数：
  1. max_holding_days: 持仓天数上限 [0, 3, 5, 10, 15, 20, 30]（0=不限制）
  2. stop_loss_pct:    单日止损阈值 [-3, -5, -7, -10]（内部替换 apply_stop_loss 的行为）

数据源：
  - MySQL（同回测脚本）
  - Tushare 代理（通过 scripts.tushare_init）

用法：
  cd backend && source .venv/bin/activate && python scripts/grid_search_overnight.py
"""

from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict
from copy import deepcopy
from typing import Any

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

sys.path.insert(0, ".")

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── 导入回测核心逻辑 ──
from scripts.backtest_chen_xiaoqun import (
    _run_backtest_with_params,
    INITIAL_CAPITAL,
)

# ── 网格参数 ──
HOLDING_DAYS_GRID = [0, 3, 5, 10, 15, 20, 30]
STOP_LOSS_PCT_GRID = [-3.0, -5.0, -7.0, -10.0]


def patch_apply_stop_loss(new_threshold: float) -> None:
    """临时替换 apply_stop_loss 中的硬编码 -5% 阈值。

    方案：替换 chen_xiaoqun_service 中 apply_stop_loss 的 stop_loss 条件。
    """
    import app.services.chen_xiaoqun_service as svc

    original_fn = svc.apply_stop_loss

    def patched_fn(trade, current_price, entry_price, days_held):
        result = original_fn(trade, current_price, entry_price, days_held)
        # 只覆盖 stop_loss 阈值，其他不变
        if result["action"] == "stop_loss":
            daily_return = (current_price - entry_price) / entry_price * 100.0
            if daily_return < new_threshold:
                return {"action": "stop_loss", "reason": f"单日跌幅 {daily_return:.1f}% > {abs(new_threshold):.0f}%，强制止损"}
        return result

    svc.apply_stop_loss = patched_fn


def restore_apply_stop_loss() -> None:
    """恢复原始 apply_stop_loss。"""
    import app.services.chen_xiaoqun_service as svc

    # reload 模块恢复原始函数
    import importlib
    importlib.reload(svc)


def run_single(
    db: Session,
    max_holding_days: int,
    stop_loss_pct: float,
) -> dict[str, Any]:
    """运行单次参数组合的回测。"""
    # 打补丁替换止损阈值
    patch_apply_stop_loss(stop_loss_pct)

    result = _run_backtest_with_params(
        db,
        max_holding_days=max_holding_days,
    )

    restore_apply_stop_loss()
    return result


def format_table(results: list[dict]) -> None:
    """格式化输出结果表。"""
    header = f"{'持仓上限':>8} {'止损%':>6} {'交易':>6} {'胜率':>6} {'平均收益':>8} {'累计收益':>8} {'最大回撤':>8} {'盈亏比':>6}"
    sep = "-" * len(header)
    print("\n" + sep)
    print("网格扫描结果")
    print(sep)
    print(header)
    print(sep)

    for r in results:
        print(
            f"{r['max_holding_days']:>8} "
            f"{r['stop_loss_pct']:>6.0f} "
            f"{r['total_trades']:>6} "
            f"{r['accuracy']:>5.1f}% "
            f"{r['average_net_return']:>7.2f}% "
            f"{r['cumulative_return']:>7.2f}% "
            f"{r['max_dd']:>7.2f}% "
            f"{r['profit_loss_ratio']:>5.2f}"
        )
    print(sep)

    # 最佳组合
    best = max(results, key=lambda r: r.get("cumulative_return", -999))
    print(f"\n最佳累计收益: 持仓上限={best['max_holding_days']}, "
          f"止损={best['stop_loss_pct']:.0f}%, "
          f"累计={best['cumulative_return']:.2f}%, "
          f"回撤={best['max_dd']:.2f}%")

    # 夏普最佳（累计/回撤）
    best_sharpe = max(
        results,
        key=lambda r: (r.get("cumulative_return", 0) / max(r.get("max_dd", 1), 0.01))
        if r.get("max_dd", 0) > 0 else -999,
    )
    print(f"最佳风险调整:  持仓上限={best_sharpe['max_holding_days']}, "
          f"止损={best_sharpe['stop_loss_pct']:.0f}%, "
          f"累计={best_sharpe['cumulative_return']:.2f}%, "
          f"回撤={best_sharpe['max_dd']:.2f}%")


def main() -> None:
    db_url = os.environ.get(
        "DATABASE_URL",
        "mysql+pymysql://aowang:1279990918@139.196.242.53:4407/ashare_workbench?charset=utf8mb4",
    )
    engine = create_engine(db_url, connect_args={"charset": "utf8mb4"}, pool_pre_ping=True)

    results: list[dict] = []
    total_combos = len(HOLDING_DAYS_GRID) * len(STOP_LOSS_PCT_GRID)
    combo_idx = 0

    with Session(engine) as db:
        for max_days in HOLDING_DAYS_GRID:
            for stop_pct in STOP_LOSS_PCT_GRID:
                combo_idx += 1
                label = f"max_days={max_days}, stop_loss={stop_pct:.0f}%"
                print(f"\n[{combo_idx}/{total_combos}] {label} ...", end=" ", flush=True)

                try:
                    r = run_single(db, max_holding_days=max_days, stop_loss_pct=stop_pct)
                    r["max_holding_days"] = max_days
                    r["stop_loss_pct"] = stop_pct
                    results.append(r)
                    print(f"交易={r.get('total_trades', '?')}, 累计={r.get('cumulative_return', '?'):.2f}%")
                except Exception as e:
                    print(f"失败: {e}")
                    logger.exception("组合 %s 执行失败", label)

    engine.dispose()

    if results:
        format_table(results)
    else:
        print("\n所有组合均失败，无结果可输出。")


if __name__ == "__main__":
    main()
