"""因子框架单测（任务 2.1）：注册表 + 拓扑排序驱动 + 契约校验。

纯 in-process：不连数据库、不连网络、不 monkeypatch 任何私有函数。用若干 dummy
因子覆盖框架契约：
- 注册与查找（含重复 id、未知 id、类装饰器、默认注册表）；
- 线性依赖顺序（拓扑序真正重排注册序）；
- 菱形依赖顺序（A<-B, A<-C, {B,C}<-D）且下游能读到上游 merge 的字段；
- 跨股上下文（snapshot / aggregates）可读；每只股票独立、入参不被修改；
- 契约越界（返回超出 output_fields）→ FactorContractError；子集允许；
- 循环依赖 → FactorCycleError（resolve/run 两个入口 + 自环）；
- depends_on 悬空 → FactorDependencyError；
- compute 原地改 stock 被拒（让 output_fields 契约闭合）。
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

import pytest

from app.services.factors import (
    BaseFactor,
    FactorContext,
    FactorContractError,
    FactorCycleError,
    FactorDependencyError,
    FactorError,
    FactorRegistry,
    FactorRegistryFrozenError,
    bootstrap_default_factor_registry,
    default_registry,
    register_factor,
    run_factors,
)


# ── 可配置 dummy 因子：结构上满足 Factor 协议，无需继承 ──────────────────
class _Dummy:
    def __init__(
        self,
        id: str,
        output_fields: tuple[str, ...] = (),
        depends_on: tuple[str, ...] = (),
        fn: Optional[Callable[[Mapping[str, Any], FactorContext], dict]] = None,
        metadata: Optional[dict] = None,
        record: Optional[list] = None,
    ) -> None:
        self.id = id
        self.output_fields = tuple(output_fields)
        self.depends_on = tuple(depends_on)
        self.metadata = metadata or {}
        self._fn = fn
        self._record = record

    def compute(self, stock: Mapping[str, Any], ctx: FactorContext) -> dict:
        if self._record is not None:
            self._record.append(self.id)
        return self._fn(stock, ctx) if self._fn is not None else {}


# ── 注册与查找 ─────────────────────────────────────────────────────────
def test_register_and_lookup():
    reg = FactorRegistry()
    factor = _Dummy("alpha", ("a",), fn=lambda s, c: {"a": 1})
    assert reg.register(factor) is factor  # 直接注册返回原实例
    assert "alpha" in reg
    assert reg.get("alpha") is factor
    assert reg.list_factors() == [factor]
    assert reg.ids() == ("alpha",)
    assert len(reg) == 1


def test_get_unknown_factor_raises():
    reg = FactorRegistry()
    with pytest.raises(FactorError):
        reg.get("does_not_exist")


def test_duplicate_id_registration_raises():
    reg = FactorRegistry()
    reg.register(_Dummy("dup", ("x",), fn=lambda s, c: {"x": 1}))
    with pytest.raises(FactorError):
        reg.register(_Dummy("dup", ("y",), fn=lambda s, c: {"y": 2}))


def test_register_as_class_decorator():
    reg = FactorRegistry()

    @reg.register
    class ClassFactor(BaseFactor):
        id = "cls_factor"
        output_fields = ("k",)

        def compute(self, stock, ctx):
            return {"k": 7}

    assert "cls_factor" in reg
    assert ClassFactor().compute({}, FactorContext()) == {"k": 7}  # 类被原样返回
    assert run_factors([{}], registry=reg)[0]["k"] == 7


def test_default_registry_requires_explicit_bootstrap_then_rejects_dynamic_registration():
    plan = bootstrap_default_factor_registry()

    assert default_registry.is_frozen is True
    assert set(plan.factor_ids) >= {"market_context", "leader"}
    with pytest.raises(FactorRegistryFrozenError):
        register_factor(_Dummy("tmp_default_reg", ("t",), fn=lambda s, c: {"t": 1}))


# ── 线性依赖顺序 ───────────────────────────────────────────────────────
def test_linear_dependency_order():
    order: list[str] = []
    reg = FactorRegistry()
    # 故意乱序注册（c, a, b），拓扑序必须重排为 a -> b -> c。
    reg.register(_Dummy("c", ("c",), depends_on=("b",), record=order,
                         fn=lambda s, c: {"c": s["b"] + 1}))
    reg.register(_Dummy("a", ("a",), record=order,
                         fn=lambda s, c: {"a": 1}))
    reg.register(_Dummy("b", ("b",), depends_on=("a",), record=order,
                         fn=lambda s, c: {"b": s["a"] + 1}))

    results = run_factors([{}], registry=reg)

    assert order == ["a", "b", "c"]
    assert results[0] == {"a": 1, "b": 2, "c": 3}


# ── 菱形依赖顺序 + 下游读到上游 merge 字段 ──────────────────────────────
def test_diamond_dependency_order_and_downstream_merge():
    order: list[str] = []
    reg = FactorRegistry()
    # 结构：A(根) <- B, A <- C, {B, C} <- D。乱序注册以证明是拓扑排序在起作用。
    reg.register(_Dummy("D", ("d",), depends_on=("B", "C"), record=order,
                         fn=lambda s, c: {"d": s["a"] + s["b"] + s["c"]}))
    reg.register(_Dummy("B", ("b",), depends_on=("A",), record=order,
                         fn=lambda s, c: {"b": s["a"] + 10}))
    reg.register(_Dummy("C", ("c",), depends_on=("A",), record=order,
                         fn=lambda s, c: {"c": s["a"] + 100}))
    reg.register(_Dummy("A", ("a",), record=order,
                         fn=lambda s, c: {"a": 1}))

    results = run_factors([{}], registry=reg)

    # A 必在最前，D 必在最后；B、C 夹在 A 与 D 之间。
    assert order[0] == "A"
    assert order[-1] == "D"
    assert order.index("A") < order.index("B") < order.index("D")
    assert order.index("A") < order.index("C") < order.index("D")
    # 同时就绪的 B、C 按注册序（B 先注册）确定性 tie-break。
    assert order == ["A", "B", "C", "D"]
    # D 读到了 A/B/C 逐层 merge 回来的字段。
    assert results[0] == {"a": 1, "b": 11, "c": 101, "d": 113}


# ── 跨股上下文可读 ─────────────────────────────────────────────────────
def test_context_snapshot_and_aggregates_readable():
    reg = FactorRegistry()
    reg.register(_Dummy(
        "uses_ctx",
        ("phase", "sector_hits"),
        fn=lambda s, c: {
            "phase": c.snapshot.get("sentiment_phase"),
            "sector_hits": c.aggregate("sector_stats", {}).get(s["sector"], 0),
        },
    ))
    ctx = FactorContext(
        snapshot={"sentiment_phase": "高潮"},
        aggregates={"sector_stats": {"AI": 3}},
    )

    results = run_factors([{"sector": "AI"}], ctx, registry=reg)

    assert results[0]["phase"] == "高潮"
    assert results[0]["sector_hits"] == 3
    assert results[0]["sector"] == "AI"  # 原有字段保留


# ── 每只股票独立 + 入参不被修改 ────────────────────────────────────────
def test_per_stock_independent_and_inputs_untouched():
    reg = FactorRegistry()
    reg.register(_Dummy("bump", ("doubled",),
                         fn=lambda s, c: {"doubled": s["v"] * 2}))
    stocks = [{"v": 1}, {"v": 5}]

    results = run_factors(stocks, registry=reg)

    assert results[0]["doubled"] == 2
    assert results[1]["doubled"] == 10
    # 入参 dict 未被写入新字段。
    assert stocks == [{"v": 1}, {"v": 5}]


def test_empty_registry_returns_copies():
    stocks = [{"a": 1}]
    results = run_factors(stocks, registry=FactorRegistry())
    assert results == [{"a": 1}]
    assert results[0] is not stocks[0]  # 是拷贝而非同一对象


# ── 契约校验：越界报错 / 子集允许 / 非 Mapping 报错 ─────────────────────
def test_contract_violation_extra_field_raises():
    reg = FactorRegistry()
    reg.register(_Dummy("leaky", ("declared",),
                         fn=lambda s, c: {"declared": 1, "sneaky": 2}))

    with pytest.raises(FactorContractError) as exc:
        run_factors([{}], registry=reg)

    assert exc.value.factor_id == "leaky"
    assert "sneaky" in exc.value.undeclared
    assert "sneaky" in str(exc.value)


def test_returning_subset_of_output_fields_is_allowed():
    reg = FactorRegistry()
    # 声明两个字段，只返回一个 —— 合法（返回 key ⊆ output_fields）。
    reg.register(_Dummy("partial", ("x", "y"), fn=lambda s, c: {"x": 1}))
    results = run_factors([{}], registry=reg)
    assert results[0] == {"x": 1}


def test_non_mapping_return_raises():
    reg = FactorRegistry()
    reg.register(_Dummy("bad_return", ("x",), fn=lambda s, c: [("x", 1)]))
    with pytest.raises(FactorContractError) as exc:
        run_factors([{}], registry=reg)
    assert exc.value.factor_id == "bad_return"


# ── 循环依赖检测 ───────────────────────────────────────────────────────
def test_cycle_detection_on_run_factors():
    reg = FactorRegistry()
    reg.register(_Dummy("B", ("b",), depends_on=("A",), fn=lambda s, c: {"b": 1}))
    reg.register(_Dummy("A", ("a",), depends_on=("B",), fn=lambda s, c: {"a": 1}))

    with pytest.raises(FactorCycleError) as exc:
        run_factors([{}], registry=reg)

    assert {"A", "B"}.issubset(set(exc.value.cycle))
    assert "A" in str(exc.value) and "B" in str(exc.value)


def test_cycle_detection_at_resolve_order():
    reg = FactorRegistry()
    reg.register(_Dummy("x", ("x",), depends_on=("y",), fn=lambda s, c: {"x": 1}))
    reg.register(_Dummy("y", ("y",), depends_on=("x",), fn=lambda s, c: {"y": 1}))
    with pytest.raises(FactorCycleError):
        reg.resolve_order()


def test_self_dependency_is_a_cycle():
    reg = FactorRegistry()
    reg.register(_Dummy("solo", ("s",), depends_on=("solo",), fn=lambda s, c: {"s": 1}))
    with pytest.raises(FactorCycleError) as exc:
        reg.resolve_order()
    assert "solo" in exc.value.cycle


# ── 悬空依赖检测 ───────────────────────────────────────────────────────
def test_dangling_dependency_raises():
    reg = FactorRegistry()
    reg.register(_Dummy("needs_ghost", ("n",), depends_on=("ghost",),
                         fn=lambda s, c: {"n": 1}))

    with pytest.raises(FactorDependencyError) as exc:
        run_factors([{}], registry=reg)

    assert exc.value.factor_id == "needs_ghost"
    assert "ghost" in exc.value.missing
    assert "ghost" in str(exc.value)


# ── compute 不得原地改 stock（让 output_fields 契约闭合）────────────────
def test_compute_cannot_mutate_stock_in_place():
    reg = FactorRegistry()

    def _mutate(stock, ctx):
        stock["hacked"] = True  # 试图绕过 output_fields 契约偷偷原地写
        return {}

    reg.register(_Dummy("mutator", (), fn=_mutate))
    with pytest.raises(TypeError):
        run_factors([{}], registry=reg)


# ── metadata 原样保留（供 2.4 通用解释）────────────────────────────────
def test_metadata_is_preserved():
    reg = FactorRegistry()
    meta = {"logic": "demo", "data_sources": ["src"], "degradation": "none"}
    reg.register(_Dummy("with_meta", ("m",), metadata=meta, fn=lambda s, c: {"m": 1}))
    assert reg.get("with_meta").metadata["logic"] == "demo"
