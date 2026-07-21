"""T3.5 出场分布诊断纯函数测试（_exit_reason_breakdown_stats）。

锁定 trailing stop「过早锁利」误判防护：未吃高点（max_return - executed_return）小 →
trailing 触发时高点在附近，非过早锁利。2026-07-22 实证 trailing_stop_hit 未吃高点
仅 +0.90%（max 3.97% vs exec 3.06%），证伪基于虚构 max_return 的过早锁利怀疑。
"""
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]


def test_breakdown_groups_by_reason():
    items = [
        ("trailing_stop_hit", 2.52, 3.42),
        ("trailing_stop_hit", 3.6, 4.5),
        ("take_profit_hit", 4.8, 9.34),
    ]
    stats = self_check._exit_reason_breakdown_stats(items)
    assert set(stats.keys()) == {"trailing_stop_hit", "take_profit_hit"}
    # trailing: avg_exec=(2.52+3.6)/2=3.06, avg_max=(3.42+4.5)/2=3.96
    assert stats["trailing_stop_hit"]["n"] == 2
    assert stats["trailing_stop_hit"]["avg_exec"] == 3.06
    assert stats["trailing_stop_hit"]["avg_max"] == 3.96
    # 未吃高点 = max - exec = 3.96 - 3.06 = 0.9
    assert stats["trailing_stop_hit"]["left_on_table"] == 0.9


def test_breakdown_left_on_table_measures_early_lock():
    """未吃高点小 = trailing 没过早锁利（高点在触发价附近）。"""
    # 过早锁利场景：exec 0.62 但 max 5.0 → 未吃高点 4.38（高，疑似过早）
    early_lock = [("trailing_stop_hit", 0.62, 5.0)]
    stats = self_check._exit_reason_breakdown_stats(early_lock)
    assert stats["trailing_stop_hit"]["left_on_table"] == 4.38  # 高 → 疑似过早

    # 正常场景：exec 3.06 max 3.97 → 未吃高点 0.91（低，非过早）
    normal = [("trailing_stop_hit", 3.06, 3.97)]
    stats = self_check._exit_reason_breakdown_stats(normal)
    assert stats["trailing_stop_hit"]["left_on_table"] == 0.91  # 低 → 非过早


def test_breakdown_handles_none_reason():
    """exit_reason=None 的项被跳过（不应进任何桶）。"""
    items = [("take_profit_hit", 4.8, 9.34), (None, 1.0, 2.0)]
    stats = self_check._exit_reason_breakdown_stats(items)
    assert set(stats.keys()) == {"take_profit_hit"}


def test_breakdown_handles_none_values():
    """executed_return/max_return 为 None 时按 0 处理（不崩溃）。"""
    items = [("stop_loss_hit", None, None)]
    stats = self_check._exit_reason_breakdown_stats(items)
    assert stats["stop_loss_hit"]["avg_exec"] == 0.0
    assert stats["stop_loss_hit"]["avg_max"] == 0.0
    assert stats["stop_loss_hit"]["left_on_table"] == 0.0


def test_breakdown_empty_returns_empty():
    assert self_check._exit_reason_breakdown_stats([]) == {}


def test_breakdown_stop_loss_high_left_on_table_is_opportunity_cost():
    """stop_loss 未吃高点高 = 止损票先涨后跌的机会成本（非 trailing 问题）。"""
    # stop_loss_hit: exec -5.69 但 max +2.61 → 未吃高点 8.3（票先涨 2.61 再跌止损）
    items = [("stop_loss_hit", -5.69, 2.61)]
    stats = self_check._exit_reason_breakdown_stats(items)
    assert stats["stop_loss_hit"]["left_on_table"] == 8.3
