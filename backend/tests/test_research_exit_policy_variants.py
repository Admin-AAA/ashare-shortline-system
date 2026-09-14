"""出场变体不许"静默无效"。

L-112 的失效模式同样适用于出场侧：如果一个变体其实没改动 `ExecutionPolicy`，
它的 Δ 会恒为 0，看起来像"该出场改动无影响"，实际是测量无效。这里对每个变体
断言它相对基线至少有一处预期差异，且差异落在预期的策略字段上。
"""
from __future__ import annotations

import dataclasses

from scripts.research_exit_policy_canonical import _variants


def test_baseline_is_production_exit_policy():
    base = _variants()["baseline"]
    assert base.take_profit.enabled is True
    assert base.take_profit.default_gain_pct == 6.0
    assert base.stop_loss.default_loss_pct == 4.5
    assert base.trailing.enabled is True
    assert base.trailing.activation_gain_pct == 2.0


def test_every_variant_changes_at_least_one_field():
    for name, ep in _variants().items():
        if name == "baseline":
            continue
        base = _variants()["baseline"]
        changed = [
            f.name for f in dataclasses.fields(base)
            if getattr(base, f.name) != getattr(ep, f.name)
        ]
        assert changed, f"变体 {name} 未改动任何字段（静默无效）"


def test_trailing_off_is_isolated():
    vs = _variants()
    base, off = vs["baseline"], vs["trail_off(只留止盈止损)"]
    assert off.trailing.enabled is False
    # 只关 trailing，止盈/止损与基线完全一致
    assert off.take_profit == base.take_profit
    assert off.stop_loss == base.stop_loss
    assert dataclasses.replace(off, trailing=base.trailing) == base


def test_take_profit_off_keeps_trailing():
    vs = _variants()
    base, off = vs["baseline"], vs["tp_off(持有到期)"]
    assert off.take_profit.enabled is False
    assert off.trailing == base.trailing
    assert off.stop_loss == base.stop_loss
