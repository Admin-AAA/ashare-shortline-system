"""list_runs hit_rate 计算口径测试（_hit_rate_from_outcome_tuples）。

锁定轻量 outcome 元组算 hit_rate 与原 is_trade_executed + is_accurate_recommendation
口径一致（2026-07-22 T4 性能优化：defer 大 JSON + 只取 4 列，hit_rate 改纯函数计算）。

口径：
- executed = entry_triggered 为真
- accurate = executed 且 resolved_trade_return > _ADDITIONAL_COST_PCT
  （resolved_trade_return = executed_return 非 None 否则 close_return）
- hit_rate = accurate / executed，executed 为空时 0.0
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.workbench_service import _hit_rate_from_outcome_tuples
from app.services.outcome_service import _ADDITIONAL_COST_PCT


def test_empty_outcomes_returns_zero():
    assert _hit_rate_from_outcome_tuples([]) == 0.0


def test_none_outcomes_skipped():
    """None outcome（无 outcome 记录的推荐）被跳过，不计入分母。"""
    assert _hit_rate_from_outcome_tuples([None, None]) == 0.0


def test_not_triggered_excluded_from_denominator():
    """entry_triggered=False 不计入 executed 分母（未成交不算 hit_rate 样本）。"""
    outcomes = [(False, 5.0, None), (True, 5.0, None)]
    # 只有1个 executed，且 accurate → hit_rate 1.0
    assert _hit_rate_from_outcome_tuples(outcomes) == 1.0


def test_all_accurate_hit_rate_1():
    """全部 executed 且净收益>成本 → hit_rate 1.0。"""
    outcomes = [(True, 5.0, None), (True, 3.5, None), (True, 10.0, None)]
    assert _hit_rate_from_outcome_tuples(outcomes) == 1.0


def test_mixed_accurate_and_failed():
    """2 accurate + 1 failed（executed_return < 成本）→ hit_rate 0.667。"""
    # _ADDITIONAL_COST_PCT ≈ 0.07；-5.69 < 0.07 失败，5.0/4.8 成功
    outcomes = [(True, 5.0, None), (True, 4.8, None), (True, -5.69, None)]
    assert _hit_rate_from_outcome_tuples(outcomes) == round(2 / 3, 3)


def test_close_return_fallback_when_executed_return_none():
    """executed_return=None 时用 close_return（与 resolved_trade_return 一致）。"""
    # executed_return=None, close_return=5.0 > 成本 → accurate
    outcomes = [(True, None, 5.0), (True, None, -3.0)]
    # 1 accurate (5.0>0.07) + 1 failed (-3.0<0.07) → 0.5
    assert _hit_rate_from_outcome_tuples(outcomes) == 0.5


def test_executed_return_exactly_at_cost_is_failed():
    """净收益 == 成本不算 accurate（> 才算，与 is_accurate_recommendation 一致）。"""
    outcomes = [(True, _ADDITIONAL_COST_PCT, None)]
    assert _hit_rate_from_outcome_tuples(outcomes) == 0.0


def test_zero_executed_returns_zero_not_division_error():
    """executed 为空（全 None 或全 not_triggered）→ 0.0，不除零。"""
    assert _hit_rate_from_outcome_tuples([(False, 5.0, None)]) == 0.0
