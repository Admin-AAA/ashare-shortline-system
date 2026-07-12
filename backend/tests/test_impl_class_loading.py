"""``impl_class`` 命令式策略装配单测（任务 3.4，需求 4.1 / 4.2 / 4.5）。

覆盖范围：
1. ``StrategySkill.impl_class`` 属性：从 manifest 读取，未声明返回 ``None``。
2. ``instantiate_strategy``：
   - manifest 有 ``impl_class`` → 成功实例化命令式类。
   - ``impl_class`` 指向的类不满足 ``Strategy`` 协议 → ``StrategyAssemblyError``，
     错误信息清晰指出 skill_id / impl_class / 原因。
   - ``impl_class`` 指向的模块 / 类不存在 → ``StrategyAssemblyError``。
   - ``impl_class`` 格式不合法（无冒号分隔）→ ``StrategyAssemblyError``。
   - 目标类实例化本身抛异常 → ``StrategyAssemblyError``。
   - 无 ``impl_class`` → 回退为 ``DeclarativeStrategy``。
3. ``build_registry_from_skills`` 混合场景：部分 skill 有 impl_class、部分没有，
   正确分派为对应实现类型。
4. 声明式路径零变化：``instantiate_strategy``/``build_registry_from_skills`` 对无
   ``impl_class`` 的 skill 的产出与 3.2 基线（直接 ``DeclarativeStrategy(skill)``）
   逐字段等价对拍。
5. ``load_skill``：命令式 skill 目录（只有 ``manifest.json``（含 impl_class）+
   ``SKILL.md``，没有 ``strategy.yaml``）能被正常加载，``contract is None`` /
   ``contract_hash is None`` / ``executable is True``（3.4 对 executable 的新增
   语义：命令式 skill 靠 impl_class 判定可执行，而非 contract）。

纯 in-process：只依赖 ``app.engine.skill_loader`` / ``app.engine.strategy`` /
``app.engine.imperative_strategy``，不吃 DB / 网络，不 import
``app.services.workbench_service``/``app.services.backtest_service``（会触发
``pymysql`` 等 DB 驱动 import，本环境不可用）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.engine.imperative_strategy import ImperativeStrategy
from app.engine.skill_loader import StrategySkill, load_skill, validate_skill
from app.engine.strategy import (
    DeclarativeStrategy,
    Strategy,
    StrategyAssemblyError,
    StrategyContext,
    build_registry_from_skills,
    instantiate_strategy,
)


# ══════════════════════════════════════════════════════════════════════════
# helpers
# ══════════════════════════════════════════════════════════════════════════
def _make_skill(
    skill_id: str = "some-skill",
    *,
    manifest: dict[str, Any] | None = None,
    contract: dict[str, Any] | None = None,
    path: Path | None = None,
) -> StrategySkill:
    """直接实例化 frozen ``StrategySkill``，绕开磁盘加载（与
    ``test_strategy_registry.py`` 的 ``_make_skill`` 同一手法）。"""
    return StrategySkill(
        skill_id=skill_id,
        name=skill_id,
        version="1.0.0",
        path=path or Path(f"/nonexistent/{skill_id}"),
        manifest=dict(manifest or {}),
        skill_frontmatter={},
        skill_body="",
        skill_hash=None,
        reference_files=[],
        contract=contract,
        contract_hash=None,
        enabled=True,
    )


def _write_command_skill_dir(tmp_path: Path, *, skill_id: str, impl_class: str | None) -> Path:
    """在临时目录下构造一个命令式 skill 目录：manifest.json（可选 impl_class）+
    SKILL.md，不写 strategy.yaml——对齐设计文档「命令式 skill 可以没有
    strategy.yaml」的落点。"""
    import json as _json

    skill_dir = tmp_path / skill_id
    skill_dir.mkdir(parents=True)
    manifest: dict[str, Any] = {"id": skill_id, "name": skill_id, "version": "1.0.0", "enabled": True}
    if impl_class is not None:
        manifest["impl_class"] = impl_class
    (skill_dir / "manifest.json").write_text(_json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(f"# {skill_id}\n\n命令式策略，无 strategy.yaml。", encoding="utf-8")
    return skill_dir


# ══════════════════════════════════════════════════════════════════════════
# 1. StrategySkill.impl_class 属性
# ══════════════════════════════════════════════════════════════════════════
def test_impl_class_property_reads_from_manifest():
    skill = _make_skill(manifest={"impl_class": "pkg.module:MyStrategy"})
    assert skill.impl_class == "pkg.module:MyStrategy"


def test_impl_class_property_none_when_absent():
    skill = _make_skill(manifest={})
    assert skill.impl_class is None


def test_impl_class_property_none_when_blank():
    skill = _make_skill(manifest={"impl_class": ""})
    assert skill.impl_class is None


# ══════════════════════════════════════════════════════════════════════════
# 2. instantiate_strategy：成功路径
# ══════════════════════════════════════════════════════════════════════════
def test_instantiate_strategy_with_valid_impl_class_returns_imperative_instance():
    skill = _make_skill(
        skill_id="fixture-skill",
        manifest={"impl_class": "_impl_class_fixtures:FixtureImperativeStrategy"},
    )

    strategy = instantiate_strategy(skill)

    assert isinstance(strategy, ImperativeStrategy)
    assert isinstance(strategy, Strategy)
    assert strategy.strategy_id == "impl-class-fixture"

    # 端到端：能正常 evaluate 出一个符合 Signal 键集合的结果。
    ctx = StrategyContext(main_industries=[], skip_universe_check=True)
    signal = strategy.evaluate({"code": "600000"}, ctx)
    assert signal["phase"] == "fixture-phase"
    assert signal["score"] == 66.0


def test_instantiate_strategy_without_impl_class_falls_back_to_declarative():
    skill = _make_skill(skill_id="declarative-skill", manifest={}, contract={"id": "declarative-skill"})

    strategy = instantiate_strategy(skill)

    assert isinstance(strategy, DeclarativeStrategy)
    assert strategy.skill is skill


# ══════════════════════════════════════════════════════════════════════════
# 3. instantiate_strategy：失败路径，报错清晰
# ══════════════════════════════════════════════════════════════════════════
def test_instantiate_strategy_rejects_class_not_satisfying_strategy_protocol():
    skill = _make_skill(
        skill_id="bad-protocol-skill",
        manifest={"impl_class": "_impl_class_fixtures:NotAStrategyAtAll"},
    )

    with pytest.raises(StrategyAssemblyError) as exc_info:
        instantiate_strategy(skill)

    message = str(exc_info.value)
    assert "bad-protocol-skill" in message
    assert "_impl_class_fixtures:NotAStrategyAtAll" in message
    assert "Strategy 协议" in message


def test_instantiate_strategy_reports_missing_module():
    skill = _make_skill(
        skill_id="missing-module-skill",
        manifest={"impl_class": "app.engine.this_module_does_not_exist:Whatever"},
    )

    with pytest.raises(StrategyAssemblyError) as exc_info:
        instantiate_strategy(skill)

    message = str(exc_info.value)
    assert "missing-module-skill" in message
    assert "app.engine.this_module_does_not_exist" in message


def test_instantiate_strategy_reports_missing_class_in_existing_module():
    skill = _make_skill(
        skill_id="missing-class-skill",
        manifest={"impl_class": "_impl_class_fixtures:ThisClassDoesNotExist"},
    )

    with pytest.raises(StrategyAssemblyError) as exc_info:
        instantiate_strategy(skill)

    message = str(exc_info.value)
    assert "missing-class-skill" in message
    assert "ThisClassDoesNotExist" in message


def test_instantiate_strategy_reports_invalid_impl_class_format():
    skill = _make_skill(
        skill_id="bad-format-skill",
        manifest={"impl_class": "no-colon-in-here"},
    )

    with pytest.raises(StrategyAssemblyError) as exc_info:
        instantiate_strategy(skill)

    assert "bad-format-skill" in str(exc_info.value)
    assert "格式不合法" in str(exc_info.value)


def test_instantiate_strategy_reports_construction_failure():
    skill = _make_skill(
        skill_id="ctor-fails-skill",
        manifest={"impl_class": "_impl_class_fixtures:RaisesOnInit"},
    )

    with pytest.raises(StrategyAssemblyError) as exc_info:
        instantiate_strategy(skill)

    message = str(exc_info.value)
    assert "ctor-fails-skill" in message
    assert "_impl_class_fixtures:RaisesOnInit" in message


# ══════════════════════════════════════════════════════════════════════════
# 4. build_registry_from_skills：混合场景正确分派
# ══════════════════════════════════════════════════════════════════════════
def test_build_registry_from_skills_dispatches_mixed_declarative_and_imperative():
    declarative_skill = _make_skill(
        skill_id="decl-1", manifest={}, contract={"id": "decl-1", "name": "声明式 1"}
    )
    imperative_skill = _make_skill(
        skill_id="impl-1",
        manifest={"impl_class": "_impl_class_fixtures:FixtureImperativeStrategy"},
    )
    another_declarative_skill = _make_skill(
        skill_id="decl-2", manifest={}, contract={"id": "decl-2", "name": "声明式 2"}
    )

    registry = build_registry_from_skills([declarative_skill, imperative_skill, another_declarative_skill])

    assert registry.ids() == ("decl-1", "impl-1", "decl-2")
    assert isinstance(registry.get("decl-1"), DeclarativeStrategy)
    assert isinstance(registry.get("impl-1"), ImperativeStrategy)
    assert isinstance(registry.get("decl-2"), DeclarativeStrategy)
    # 每个实例仍满足统一的 Strategy 协议，aggregator/调用方无需区分来源。
    for skill_id in registry.ids():
        assert isinstance(registry.get(skill_id), Strategy)


def test_build_registry_from_skills_propagates_assembly_error_for_bad_impl_class():
    bad_skill = _make_skill(
        skill_id="bad-skill",
        manifest={"impl_class": "_impl_class_fixtures:NotAStrategyAtAll"},
    )

    with pytest.raises(StrategyAssemblyError):
        build_registry_from_skills([bad_skill])


# ══════════════════════════════════════════════════════════════════════════
# 5. 声明式路径零变化：与 3.2 基线（直接包 DeclarativeStrategy）逐字段等价对拍
# ══════════════════════════════════════════════════════════════════════════
def test_declarative_path_unchanged_via_instantiate_strategy():
    """无 impl_class 时，``instantiate_strategy`` 的产出必须与 3.2 基线
    ``DeclarativeStrategy(skill)`` 在「委托同一个 skill、evaluate 结果逐字段一致」
    意义下等价——这是需求 4.2「回退为 DeclarativeStrategy，且声明式 YAML 路径行为
    逐字段不变」的直接验证。"""
    contract = {
        "id": "equiv-skill",
        "name": "等价对拍策略",
        "weight": 1.0,
        "universe": {"include": ["amount_yi >= 1"]},
        "scoring": {"base": 50, "rules": []},
    }
    skill = _make_skill(skill_id="equiv-skill", manifest={}, contract=contract)

    baseline = DeclarativeStrategy(skill)
    via_instantiate = instantiate_strategy(skill)

    assert isinstance(via_instantiate, DeclarativeStrategy)
    assert via_instantiate.skill is baseline.skill is skill

    ctx = StrategyContext(main_industries=[], skip_universe_check=False)
    stock = {
        "code": "600000",
        "amount_yi": 5.0,
        "market_sample_count": 200,
        "data_completeness_score": 100,
        "evidence_quality_score": 100,
    }
    baseline_signal = baseline.evaluate(dict(stock), ctx)
    via_instantiate_signal = via_instantiate.evaluate(dict(stock), ctx)

    assert via_instantiate_signal == baseline_signal
    assert list(via_instantiate_signal.keys()) == list(baseline_signal.keys())


def test_build_registry_from_skills_all_declarative_matches_3_2_baseline():
    """本任务改造前（3.2）``build_registry_from_skills`` 对全声明式 skill 列表的行为
    是「无条件包 DeclarativeStrategy」；改造后（调用 instantiate_strategy）对这批
    没有 impl_class 的 skill 必须产出完全相同的注册表结构。"""
    skills = [
        _make_skill(skill_id="skill-1", manifest={}, contract={"id": "skill-1"}),
        _make_skill(skill_id="skill-2", manifest={}, contract={"id": "skill-2"}),
        _make_skill(skill_id="skill-3", manifest={}, contract={"id": "skill-3"}),
    ]

    registry = build_registry_from_skills(skills)

    assert registry.ids() == ("skill-1", "skill-2", "skill-3")
    for skill in skills:
        strategy = registry.get(skill.skill_id)
        assert isinstance(strategy, DeclarativeStrategy)
        assert strategy.skill is skill


# ══════════════════════════════════════════════════════════════════════════
# 6. load_skill：命令式 skill 目录（无 strategy.yaml）能被正常加载
# ══════════════════════════════════════════════════════════════════════════
def test_load_skill_command_style_directory_without_strategy_yaml(tmp_path):
    skill_dir = _write_command_skill_dir(
        tmp_path, skill_id="cmd-skill", impl_class="_impl_class_fixtures:FixtureImperativeStrategy"
    )

    skill = load_skill(skill_dir)

    assert skill.skill_id == "cmd-skill"
    assert skill.impl_class == "_impl_class_fixtures:FixtureImperativeStrategy"
    assert skill.contract is None
    assert skill.contract_hash is None
    # 3.4 新增语义：命令式 skill 靠 impl_class 判定可执行，而非 contract。
    assert skill.executable is True
    assert skill.agent_enabled is True

    # validate_skill 现有「contract is None 时提前返回」逻辑天然支持命令式路径：
    # 只要 SKILL.md 存在且非空，就不应报错。
    errors = validate_skill(skill)
    assert errors == []

    # 端到端：装配 + evaluate 正常工作。
    strategy = instantiate_strategy(skill)
    assert isinstance(strategy, ImperativeStrategy)


def test_load_skill_command_style_without_impl_class_is_not_executable_without_contract():
    """既没有 impl_class 也没有 strategy.yaml 的目录（纯 Agent 审查型 skill）：
    executable 应为 False——没有可执行的评分实现（既非声明式合同，也非命令式类），
    这与命令式路径新增的「impl_class 存在即可执行」语义不冲突：可执行的判定
    始终要求"有一个可 evaluate 的实现"，纯 Agent skill 没有。"""
    skill = _make_skill(skill_id="agent-only-skill", manifest={}, contract=None)
    assert skill.executable is False
    assert skill.impl_class is None
