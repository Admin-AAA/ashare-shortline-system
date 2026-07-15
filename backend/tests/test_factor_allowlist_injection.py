"""因子 output_fields → evaluator.ALLOWED_NAMES 自动注入闭环单测（任务 2.2）。

纯 in-process：不连数据库 / 网络，不 monkeypatch 任何私有函数。用声明了若干
``output_fields`` 的 dummy 因子注册进**独立 FactorRegistry**，覆盖：

- 汇集 output_fields（并集，跨多因子；空因子不产生噪声）；
- 注入到「新 set」→ 字段并入、就地更新、且不污染全局白名单；
- **幂等**：重复注入结果稳定、无副作用；
- **只增不删**：传入含既有名的 set，注入后既有条目仍在（保护 evaluator 硬编码项）；
- 默认 ``allowlist=None`` 时作用于真实 ``evaluator.ALLOWED_NAMES``（save/restore 还原）；
- 端到端闭环：注入后新字段可被 ``evaluator.evaluate`` 合法引用（注入前不可）；
- ``bootstrap_factor_allowlist`` 经默认注册表注入全局白名单。

凡触碰全局 ``evaluator.ALLOWED_NAMES`` 的用例，均用 ``allowlist_snapshot`` 夹具在
测试结束后原样还原，保证不留污染。
"""
from __future__ import annotations

from typing import Any, Mapping

import pytest

from app.engine import evaluator
from app.services.factors import (
    FactorContext,
    FactorRegistry,
    bootstrap_factor_allowlist,
    collect_factor_output_fields,
    default_registry,
    inject_factor_output_fields,
)


# ── dummy 因子：结构上满足 Factor 协议，仅声明 output_fields（compute 与本任务无关）─
class _Dummy:
    def __init__(self, id: str, output_fields: tuple[str, ...] = ()) -> None:
        self.id = id
        self.output_fields = tuple(output_fields)
        self.depends_on: tuple[str, ...] = ()
        self.metadata: Mapping[str, Any] = {}

    def compute(self, stock: Mapping[str, Any], ctx: FactorContext) -> dict:
        return {}


@pytest.fixture
def allowlist_snapshot():
    """快照并在测试后原样还原全局 ``evaluator.ALLOWED_NAMES``（就地清空 + 回填，
    保证还原到同一集合对象，不影响其它持有引用者）。"""
    saved = set(evaluator.ALLOWED_NAMES)
    try:
        yield saved
    finally:
        evaluator.ALLOWED_NAMES.clear()
        evaluator.ALLOWED_NAMES.update(saved)


# ── 汇集 output_fields ─────────────────────────────────────────────────
def test_collect_output_fields_is_union_across_factors():
    reg = FactorRegistry()
    reg.register(_Dummy("f1", ("alpha", "beta")))
    reg.register(_Dummy("f2", ("gamma",)))
    reg.register(_Dummy("f3", ()))  # 无输出字段：不应引入任何噪声
    assert collect_factor_output_fields(reg) == {"alpha", "beta", "gamma"}


def test_collect_output_fields_empty_registry():
    assert collect_factor_output_fields(FactorRegistry()) == set()


# ── 注入到「新 set」：并入 + 就地更新 + 不污染全局 ──────────────────────
def test_inject_into_new_set_merges_and_does_not_touch_global():
    reg = FactorRegistry()
    reg.register(_Dummy("f1", ("factor_field_a", "factor_field_b")))
    reg.register(_Dummy("f2", ("factor_field_c",)))
    target: set[str] = set()
    global_before = set(evaluator.ALLOWED_NAMES)

    result = inject_factor_output_fields(registry=reg, allowlist=target)

    # 就地更新并返回同一对象
    assert result is target
    assert target == {"factor_field_a", "factor_field_b", "factor_field_c"}
    # 全局白名单完全未变（新字段没有泄漏进去）
    assert evaluator.ALLOWED_NAMES == global_before
    assert "factor_field_a" not in evaluator.ALLOWED_NAMES


# ── 幂等 ───────────────────────────────────────────────────────────────
def test_injection_is_idempotent():
    reg = FactorRegistry()
    reg.register(_Dummy("f1", ("x1", "x2")))
    target: set[str] = set()

    first = set(inject_factor_output_fields(registry=reg, allowlist=target))
    second = set(inject_factor_output_fields(registry=reg, allowlist=target))

    assert first == second == {"x1", "x2"}
    # 多次调用后目标集合大小稳定，无重复副作用
    assert target == {"x1", "x2"}


# ── 只增不删：既有条目在注入后仍保留 ───────────────────────────────────
def test_injection_preserves_existing_entries():
    reg = FactorRegistry()
    reg.register(_Dummy("f1", ("new_field",)))
    # 目标里已有若干既存名（模拟 evaluator 现有硬编码条目）
    target = {"existing_1", "existing_2"}

    inject_factor_output_fields(registry=reg, allowlist=target)

    assert {"existing_1", "existing_2"}.issubset(target)  # 既有条目未被删
    assert "new_field" in target                          # 新字段已并入


def test_injection_with_overlapping_names_keeps_all():
    reg = FactorRegistry()
    reg.register(_Dummy("f1", ("shared", "brand_new")))
    target = {"shared", "existing"}  # 与因子字段有交集

    inject_factor_output_fields(registry=reg, allowlist=target)

    # 交集不引发删除或重复，最终是三者并集
    assert target == {"shared", "existing", "brand_new"}


# ── 默认 allowlist=None → 作用于真实 evaluator.ALLOWED_NAMES ─────────────
def test_default_targets_evaluator_allowlist(allowlist_snapshot):
    reg = FactorRegistry()
    reg.register(_Dummy("f1", ("injected_into_global_field",)))
    assert "injected_into_global_field" not in evaluator.ALLOWED_NAMES

    result = inject_factor_output_fields(registry=reg)  # allowlist 缺省 → 全局白名单

    # 就地更新的正是全局集合本体
    assert result is evaluator.ALLOWED_NAMES
    assert "injected_into_global_field" in evaluator.ALLOWED_NAMES
    # 既有硬编码条目全部保留（未删任何原字段）
    assert allowlist_snapshot.issubset(evaluator.ALLOWED_NAMES)


# ── 端到端闭环：注入后字段可被 evaluate 引用（注入前不可）───────────────
def test_injected_field_becomes_referenceable_in_evaluator(allowlist_snapshot):
    field = "e2e_factor_field"
    expr = f"{field} > 0"

    # 注入前：字段不在白名单 → evaluate 视为非法名 → 静默判 False（策略死代码）
    assert field not in evaluator.ALLOWED_NAMES
    assert evaluator.evaluate(expr, {field: 5}) is False

    reg = FactorRegistry()
    reg.register(_Dummy("e2e", (field,)))
    inject_factor_output_fields(registry=reg)

    # 注入后：字段进入白名单 → 表达式可正常引用求值
    assert field in evaluator.ALLOWED_NAMES
    assert evaluator.evaluate(expr, {field: 5}) is True
    assert evaluator.evaluate(expr, {field: -1}) is False


# ── bootstrap 接线入口：经默认注册表注入全局白名单 ─────────────────────
def test_bootstrap_injects_a_completed_registry_into_global(allowlist_snapshot):
    field = "bootstrap_field"
    registry = FactorRegistry()
    registry.register(_Dummy("bootstrap_dummy", (field,)))
    assert field not in evaluator.ALLOWED_NAMES

    result = bootstrap_factor_allowlist(registry)

    assert result is evaluator.ALLOWED_NAMES
    assert field in evaluator.ALLOWED_NAMES
