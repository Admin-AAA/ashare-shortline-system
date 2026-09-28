"""确认路径的策略信号构造：universe 门槛只由一条通道判定。

``run_strategy`` 自己查 universe 时会短路成 ``hard_veto`` + "不在策略股票池"，把具体哪条
规则不过的信息丢掉。确认路径必须让 ``explain_skill_prefilter`` 独占门槛判定，否则盘中累计
量的可判定性判据会被这条粗粒度否决绕过。
"""

from pathlib import Path

from app.engine.skill_loader import StrategySkill
from app.services.execution_confirmation import _build_signal_for_strategy


def _skill(contract: dict) -> StrategySkill:
    return StrategySkill(
        skill_id="test-strategy",
        name="测试策略",
        version="0.1.0",
        path=Path("/nonexistent"),
        manifest={"mode": "declarative", "status": "active", "capabilities": [], "requirements": []},
        skill_frontmatter={},
        skill_body="",
        skill_hash=None,
        contract=contract,
        contract_hash=None,
        enabled=True,
        reference_files=[],
    )


_CONTRACT = {
    "weight": 1.0,
    "default_entry_mode": "next_open",
    "universe": {"include": ["amount_yi >= 3"], "exclude": ["is_st == true"]},
    "phases": [
        {"name": "退潮加速", "when": "pos60 > 97 and dist60 > 32", "hard_veto": True,
         "risk_flags": ["加速末端"]},
    ],
    "setups": [
        {"name": "平台突破", "when": "breakout20 == 1", "entry_rule": "放量突破平台",
         "invalid_rule": "跌破 MA20"},
    ],
}


def _stock(**overrides) -> dict:
    stock = {
        "code": "300666",
        "name": "江丰电子",
        "industry": "半导体",
        "amount_yi": 2.601,
        "is_st": False,
        "breakout20": 1,
        "pos60": 80.0,
        "dist60": 10.0,
        "turnover_rate": 3.0,
    }
    stock.update(overrides)
    return stock


def test_universe_shortfall_is_reported_as_prefilter_not_hard_veto():
    """累计成交额不足只体现为 prefilter 未通过，带着具体规则与观测值。"""
    signal, prefilter_ok, reason = _build_signal_for_strategy(
        _skill(_CONTRACT), _stock(), {"main_industries": ["半导体"]}
    )

    assert prefilter_ok is False
    assert signal["hard_veto"] is False
    assert signal["prefilter_detail"]["expression"] == "amount_yi >= 3"
    assert signal["prefilter_detail"]["observed"] == {"amount_yi": 2.601}
    assert "3" in reason


def test_phase_hard_veto_still_applies():
    """phases 里的硬否决不受影响，仍然照常生效。"""
    signal, prefilter_ok, _reason = _build_signal_for_strategy(
        _skill(_CONTRACT),
        _stock(amount_yi=9.0, pos60=98.0, dist60=35.0),
        {"main_industries": ["半导体"]},
    )

    assert prefilter_ok is True
    assert signal["hard_veto"] is True
    assert signal["phase"] == "退潮加速"


def test_universe_exclude_is_reported_with_its_own_rule():
    """universe.exclude 命中同样带出具体规则，而不是笼统的股票池否决。"""
    signal, prefilter_ok, _reason = _build_signal_for_strategy(
        _skill(_CONTRACT),
        _stock(amount_yi=9.0, is_st=True),
        {"main_industries": ["半导体"]},
    )

    assert prefilter_ok is False
    assert signal["prefilter_detail"]["scope"] == "universe_exclude"
    assert signal["prefilter_detail"]["expression"] == "is_st == true"


def test_qualified_stock_produces_setup_signal():
    signal, prefilter_ok, reason = _build_signal_for_strategy(
        _skill(_CONTRACT), _stock(amount_yi=9.0), {"main_industries": ["半导体"]}
    )

    assert prefilter_ok is True
    assert reason == "pass"
    assert signal["setup_match"] == "平台突破"
    assert signal["hard_veto"] is False
