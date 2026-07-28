"""单股分析必须复用正式推荐管线的 strategy.yaml 规则评分。"""
from __future__ import annotations

import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.engine.skill_loader import load_skill
from app.services.single_stock_analysis import (
    _evaluate_single_stock_rule_signals,
    _run_llm_analysis,
)


def test_single_stock_rule_signals_are_real_strategy_results():
    skill = load_skill(BACKEND_ROOT / "skills" / "daily-stock-analysis-skill")
    stock = {
        "code": "000001",
        "name": "平安银行",
        "industry": "银行",
        "dsa_signal_score": 79.0,
        "dsa_trend_score": 26.0,
        "dsa_bias_score": 20.0,
        "dsa_volume_score": 10.0,
        "dsa_support_score": 10.0,
        "dsa_macd_score": 5.0,
        "dsa_rsi_score": 8.0,
        "data_completeness_score": 100.0,
        "evidence_quality_score": 100.0,
        "market_sample_count": 5000,
    }

    signals = _evaluate_single_stock_rule_signals(
        stock,
        [{"skill_id": skill.skill_id, "skill": skill, "weight": 1.0}],
        {"main_industries": ["银行"]},
    )

    signal = signals[skill.skill_id]
    assert signal is not None
    assert signal["strategy_id"] == skill.skill_id
    assert 0 <= signal["score"] <= 100
    assert "score_cap" in signal
    assert "hard_veto" in signal


def test_single_skill_llm_mode_also_returns_pipeline_final_score(monkeypatch):
    async def fake_reviews(**kwargs):
        skill = kwargs["skills"][0]
        return [{
            "skill_id": skill.skill_id,
            "skill_name": skill.name,
            "review": {
                "score": 74.0,
                "confidence": 0.8,
                "stance": "support",
                "score_delta": 16.0,
                "summary": "规则与深审方向一致",
                "action": "观察",
                "supports": ["趋势结构"],
                "risks": [],
            },
            "hallucination_check": None,
            "error": None,
        }]

    monkeypatch.setattr("app.agents.review_runner.run_skill_reviews", fake_reviews)
    monkeypatch.setattr("app.agents.agno_skill_agent.drain_api_costs", lambda: [])
    monkeypatch.setattr("app.services.workbench_service._load_calibrated_agg_weights", lambda db: None)

    stock = {
        "code": "000001",
        "name": "平安银行",
        "industry": "银行",
        "price": 10.0,
        "amount_yi": 8.0,
        "turnover_rate": 3.0,
        "announcement_negative_count": 0,
        "dsa_signal_score": 79.0,
        "dsa_trend_score": 26.0,
        "dsa_bias_score": 20.0,
        "dsa_volume_score": 10.0,
        "dsa_support_score": 10.0,
        "dsa_macd_score": 5.0,
        "dsa_rsi_score": 8.0,
        "data_completeness_score": 100.0,
        "evidence_quality_score": 100.0,
        "market_sample_count": 5000,
        "data_authenticity": 1.0,
    }
    snapshot = {
        "sample_count": 5000,
        "up_count": 2800,
        "limit_up_count": 40,
        "limit_down_count": 5,
        "main_industries": ["银行"],
        "breadth": {"regime": "偏多"},
    }

    result = _run_llm_analysis(
        object(), "000001", stock, "daily-stock-analysis-skill", snapshot,
    )

    assert result["status"] == "ok"
    assert 0 <= result["final_score"] <= 100
    assert result["rule_score"] <= 100
    assert result["component_scores"]
