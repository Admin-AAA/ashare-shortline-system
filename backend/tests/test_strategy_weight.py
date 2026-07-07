"""Tests for per-skill empirical weight wiring (T5 第131轮).

Regression guard: strategy.yaml 顶层 `weight` 字段必须注入 signal，
aggregator 才能按历史胜率加权；否则 8 策略永远等权，净负策略污染 final_score。
"""
import pathlib

import pytest

from app.engine.aggregator import _compute_strategy_rule_score
from app.engine.skill_loader import load_skill
from app.engine.strategy_runner import run_strategy


SKILLS_DIR = pathlib.Path(__file__).resolve().parents[1] / "skills"


def _load(skill_id: str):
    return load_skill(SKILLS_DIR / skill_id)


def _stock(industry="银行", **kwargs):
    return {
        "code": "600000",
        "name": "测试",
        "industry": industry,
        "data_completeness_score": 100,
        "evidence_quality_score": 100,
        "market_sample_count": 100,
        **kwargs,
    }


def test_signal_carries_contract_weight():
    """run_strategy 必须把 strategy.yaml 的 weight 注入 signal.weight。"""
    sk = _load("chao-gu-yang-jia-skill")
    assert sk.contract.get("weight") == 1.20
    out = run_strategy(sk, _stock(main_industries=["银行"]), main_industries=["银行"])
    assert "weight" in out
    assert out["weight"] == 1.20


def test_vetoed_signal_still_carries_weight():
    """即使被 universe 否决，weight 也应随 signal 透传（不影响等权默认 1.0）。"""
    sk = _load("high-range-breakout-momentum")
    # 用一个几乎不可能通过 universe 的空 stock
    out = run_strategy(sk, _stock(industry="未分类"), main_industries=[])
    assert out["weight"] == 0.35


def test_compute_strategy_rule_score_respects_weight():
    """加权均值应随 weight 变化：降低某 signal 权重会拉低其贡献。"""
    base = [
        {"strategy_id": "a", "score": 80.0, "weight": 1.0},
        {"strategy_id": "b", "score": 40.0, "weight": 1.0},
    ]
    equal = _compute_strategy_rule_score(base)
    assert equal == 60.0
    # 把低分 b 权重降到 0.1 → 均值应上移
    down = [
        {"strategy_id": "a", "score": 80.0, "weight": 1.0},
        {"strategy_id": "b", "score": 40.0, "weight": 0.1},
    ]
    lowered = _compute_strategy_rule_score(down)
    assert lowered > equal
    assert lowered == pytest.approx((80.0 * 1.0 + 40.0 * 0.1) / 1.1)
