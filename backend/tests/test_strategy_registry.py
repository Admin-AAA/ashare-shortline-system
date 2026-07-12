"""``Strategy`` 协议 + ``DeclarativeStrategy`` + ``StrategyRegistry`` 单测（任务 3.2，需求 4.4）。

覆盖范围：
1. ``StrategyRegistry`` 注册 / 查找 / 未知 id 报错（结构性行为，对齐 2.1 的
   ``FactorRegistry`` 用例手法）。
2. ``DeclarativeStrategy.evaluate`` 与直接调用 ``run_strategy`` 的结果**逐字段一致**
   ——复用 3.1 ``test_signal_equivalence.py`` 的等价对拍手法：手写内联 contract，
   不从磁盘 / yaml 加载，直接实例化 frozen ``StrategySkill``。
3. ``build_registry_from_skills`` 批量注册正确（skill_id 到 ``DeclarativeStrategy``
   的映射、重复 id 报错等）。

纯 in-process：只依赖 ``app.engine.strategy`` / ``app.engine.strategy_runner`` /
``app.engine.skill_loader`` / ``app.engine.signal``，不吃 DB / 网络，不 import
``app.services.workbench_service`` / ``app.services.backtest_service``（它们会触发
``pymysql`` 等 DB 驱动 import，本环境不可用）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.engine.signal import SIGNAL_FIELDS
from app.engine.skill_loader import StrategySkill
from app.engine.strategy import (
    DeclarativeStrategy,
    DuplicateStrategyError,
    Strategy,
    StrategyContext,
    StrategyNotFoundError,
    StrategyRegistry,
    build_registry_from_skills,
)
from app.engine.strategy_runner import run_strategy

# ── 内联 contract：与 3.1 test_signal_equivalence.py 同一手法，覆盖 universe /
#    prefilter / phases / scoring / setups / risk 的完整评分路径。 ──────────
_CONTRACT: dict = {
    "id": "registry-equiv-strategy",
    "name": "注册表等价测试策略",
    "weight": 1.1,
    "meta": {"strategy_independence": 0.88},
    "universe": {
        "include": ["amount_yi >= 1"],
        "exclude": ["is_st == true"],
    },
    "prefilter": {"market": {"exclude": ["avg_cost > 200"]}},
    "phases": [
        {"when": "pct >= 5", "name": "上涨", "score_cap": 90, "hard_veto": False, "risk_flags": ["涨幅偏高"]},
    ],
    "scoring": {
        "base": 50,
        "score_fields": [
            {"field": "pct", "multiplier": 2.0, "cap": 20, "reason": "涨幅贡献"},
        ],
        "rules": [
            {"when": "vol5_20 >= 1", "delta": 10, "reason": "量比放大"},
        ],
    },
    "setups": [
        {"when": "breakout20 == true", "name": "突破买点", "score_delta": 6,
         "entry_rule": "突破确认后进场", "invalid_rule": "跌破 MA20"},
    ],
    "risk": {
        "rules": [
            {"when": "bias_ma20 >= 15", "flag": "乖离率偏高", "level": "high"},
        ],
    },
    "require_setup": False,
}

_CONTRACT_HASH = "fedcba9876543210fedcba9876543210"


def _make_skill(skill_id: str = "registry-equiv-strategy", contract: dict | None = None) -> StrategySkill:
    """直接实例化 frozen ``StrategySkill``，绕开磁盘 / yaml 加载。"""
    contract = dict(contract or _CONTRACT)
    contract["id"] = skill_id
    return StrategySkill(
        skill_id=skill_id,
        name=contract.get("name", skill_id),
        version="1.0.0",
        path=Path(f"/nonexistent/{skill_id}"),  # run_strategy 不访问 path
        manifest={},
        skill_frontmatter={},
        skill_body="",
        skill_hash=None,
        reference_files=[],
        contract=contract,
        contract_hash=_CONTRACT_HASH,
        enabled=True,
    )


def _full_quality(**extra) -> dict:
    base = {
        "code": "600000",
        "name": "测试股",
        "market_sample_count": 200,
        "data_completeness_score": 100,
        "evidence_quality_score": 100,
    }
    base.update(extra)
    return base


# ══════════════════════════════════════════════════════════════════════════
# 1. StrategyRegistry：注册 / 查找 / 未知 id 报错
# ══════════════════════════════════════════════════════════════════════════
class _StubStrategy:
    """最小 Strategy 协议实现，仅用于验证注册表结构性行为，不涉及真实评分逻辑。"""

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def evaluate(self, stock, ctx):  # noqa: ANN001 - 测试替身，签名对齐协议即可
        return {"tag": self.tag}


def test_register_and_get_roundtrip():
    registry = StrategyRegistry()
    stub = _StubStrategy("a")

    returned = registry.register("skill-a", stub)

    assert returned is stub
    assert registry.get("skill-a") is stub
    assert "skill-a" in registry
    assert len(registry) == 1


def test_register_duplicate_skill_id_raises():
    registry = StrategyRegistry()
    registry.register("skill-a", _StubStrategy("a"))

    with pytest.raises(DuplicateStrategyError):
        registry.register("skill-a", _StubStrategy("b"))


def test_get_unknown_skill_id_raises():
    registry = StrategyRegistry()

    with pytest.raises(StrategyNotFoundError):
        registry.get("does-not-exist")


def test_list_strategies_and_ids_preserve_registration_order():
    registry = StrategyRegistry()
    s_a, s_b, s_c = _StubStrategy("a"), _StubStrategy("b"), _StubStrategy("c")
    registry.register("skill-a", s_a)
    registry.register("skill-b", s_b)
    registry.register("skill-c", s_c)

    assert registry.list_strategies() == [s_a, s_b, s_c]
    assert registry.ids() == ("skill-a", "skill-b", "skill-c")


def test_unregister_removes_entry_and_is_idempotent_for_missing_id():
    registry = StrategyRegistry()
    registry.register("skill-a", _StubStrategy("a"))

    registry.unregister("skill-a")
    assert "skill-a" not in registry
    # 移除不存在的 id 不应报错（幂等）。
    registry.unregister("skill-a")


def test_register_rejects_object_without_evaluate():
    registry = StrategyRegistry()

    class _NotAStrategy:
        pass

    with pytest.raises(Exception):
        registry.register("skill-x", _NotAStrategy())


# ══════════════════════════════════════════════════════════════════════════
# 2. DeclarativeStrategy.evaluate 与直接 run_strategy 结果逐字段一致
# ══════════════════════════════════════════════════════════════════════════
_EQUIV_SCENARIOS: list[dict] = [
    {
        "name": "universe_veto",
        "stock": _full_quality(amount_yi=0.5, pct=8, breakout20=True),
        "main_industries": [],
        "skip_universe_check": False,
    },
    {
        "name": "full_scoring_phase_setup_risk",
        "stock": _full_quality(
            amount_yi=5, is_st=False, avg_cost=50, pct=8, vol5_20=1.5,
            breakout20=True, bias_ma20=20,
        ),
        "main_industries": ["半导体"],
        "skip_universe_check": False,
    },
    {
        "name": "skip_universe_check_true",
        "stock": _full_quality(
            amount_yi=0.5, is_st=False, pct=3, vol5_20=0.5,
            breakout20=False, bias_ma20=5,
        ),
        "main_industries": [],
        "skip_universe_check": True,
    },
    {
        "name": "coverage_cap_downgrade",
        "stock": _full_quality(
            amount_yi=5, is_st=False, avg_cost=50, pct=8, vol5_20=1.5,
            breakout20=True, bias_ma20=5,
            market_sample_count=10,
        ),
        "main_industries": [],
        "skip_universe_check": False,
    },
]


@pytest.mark.parametrize("scenario", _EQUIV_SCENARIOS, ids=lambda s: s["name"])
def test_declarative_strategy_evaluate_matches_run_strategy_byte_for_byte(scenario):
    """DeclarativeStrategy.evaluate 委托 run_strategy，产出必须逐字段一致（零行为变化）。"""
    skill = _make_skill()
    strategy = DeclarativeStrategy(skill)
    ctx = StrategyContext(
        main_industries=scenario["main_industries"],
        skip_universe_check=scenario["skip_universe_check"],
    )

    direct = run_strategy(
        skill,
        dict(scenario["stock"]),
        main_industries=scenario["main_industries"],
        skip_universe_check=scenario["skip_universe_check"],
    )
    via_strategy = strategy.evaluate(dict(scenario["stock"]), ctx)

    assert via_strategy == direct, f"[{scenario['name']}] DeclarativeStrategy 与直接 run_strategy 结果不一致"
    # 键序也必须一致（防止委托层意外重排/丢字段）。
    assert list(via_strategy.keys()) == list(direct.keys()) == list(SIGNAL_FIELDS)


def test_declarative_strategy_skill_id_property():
    skill = _make_skill(skill_id="registry-equiv-strategy")
    strategy = DeclarativeStrategy(skill)
    assert strategy.skill_id == "registry-equiv-strategy"


def test_declarative_strategy_satisfies_strategy_protocol():
    skill = _make_skill()
    strategy = DeclarativeStrategy(skill)
    assert isinstance(strategy, Strategy)


# ══════════════════════════════════════════════════════════════════════════
# 3. build_registry_from_skills：批量注册正确
# ══════════════════════════════════════════════════════════════════════════
def test_build_registry_from_skills_registers_all_as_declarative():
    skills = [
        _make_skill(skill_id="skill-1"),
        _make_skill(skill_id="skill-2"),
        _make_skill(skill_id="skill-3"),
    ]

    registry = build_registry_from_skills(skills)

    assert registry.ids() == ("skill-1", "skill-2", "skill-3")
    for skill in skills:
        strategy = registry.get(skill.skill_id)
        assert isinstance(strategy, DeclarativeStrategy)
        assert strategy.skill is skill


def test_build_registry_from_skills_empty_list_yields_empty_registry():
    registry = build_registry_from_skills([])
    assert len(registry) == 0
    assert registry.ids() == ()


def test_build_registry_from_skills_then_evaluate_matches_run_strategy():
    """端到端：build_registry_from_skills 产出的 registry，经 get().evaluate() 取得的
    Signal 与直接 run_strategy 结果一致——这正是 workbench_service/backtest_service
    改造点的核心不变量。"""
    skill = _make_skill(skill_id="e2e-skill")
    registry = build_registry_from_skills([skill])
    stock = _full_quality(amount_yi=5, is_st=False, avg_cost=50, pct=8, vol5_20=1.5, breakout20=True, bias_ma20=5)
    ctx = StrategyContext(main_industries=["半导体"], skip_universe_check=False)

    via_registry = registry.get("e2e-skill").evaluate(dict(stock), ctx)
    direct = run_strategy(skill, dict(stock), main_industries=["半导体"], skip_universe_check=False)

    assert via_registry == direct
