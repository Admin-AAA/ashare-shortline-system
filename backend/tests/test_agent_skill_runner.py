from pathlib import Path

import pytest

from app.engine.agent_skill_runner import normalize_agent_review, run_agent_skill
from app.engine.skill_loader import StrategySkill


def _skill() -> StrategySkill:
    return StrategySkill(
        skill_id="test-skill",
        name="测试 Skill",
        version="0.1.0",
        path=Path(__file__).parent,
        manifest={},
        skill_frontmatter={},
        skill_body="## 原则\n- 只做真实数据审查",
        skill_hash="abc123",
        reference_files=[],
        contract=None,
        contract_hash=None,
        enabled=True,
    )


def test_agent_skill_requires_llm_runtime(monkeypatch):
    settings = type(
        "Settings",
        (),
        {
            "agent_skill_runtime": "local",
            "agent_skill_api_key": "sk-test",
            "agent_skill_prompt_char_limit": 22000,
        },
    )()
    monkeypatch.setattr("app.engine.agent_skill_runner.get_settings", lambda: settings)

    with pytest.raises(RuntimeError, match="强制真实 LLM"):
        run_agent_skill(_skill(), {"code": "000001"}, {"breadth": {}})


def test_agent_skill_requires_api_key(monkeypatch):
    settings = type(
        "Settings",
        (),
        {
            "agent_skill_runtime": "llm",
            "agent_skill_api_key": "",
            "agent_skill_prompt_char_limit": 22000,
        },
    )()
    monkeypatch.setattr("app.engine.agent_skill_runner.get_settings", lambda: settings)

    with pytest.raises(RuntimeError, match="ASHARE_AGENT_SKILL_API_KEY"):
        run_agent_skill(_skill(), {"code": "000001"}, {"breadth": {}})


def test_agent_review_schema_is_strict():
    with pytest.raises(RuntimeError, match="缺少字段: score_delta"):
        normalize_agent_review(
            {
                "stance": "neutral",
                "confidence": 0.5,
                "lenses": [],
                "supports": [],
                "risks": [],
                "summary": "中性观察",
            }
        )

    with pytest.raises(RuntimeError, match="超出范围"):
        normalize_agent_review(
            {
                "stance": "support",
                "score_delta": 99,
                "confidence": 0.5,
                "lenses": [],
                "supports": [],
                "risks": [],
                "summary": "支持",
            }
        )
