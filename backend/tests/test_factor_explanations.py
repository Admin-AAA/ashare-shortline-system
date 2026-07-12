"""因子解释通用生成单测（任务 2.4，需求 3.1）。

纯 in-process：用隔离的 ``FactorRegistry`` + dummy 因子验证 ``explanations`` 这个
deep function 的通用契约（不污染 default_registry，也不依赖真实短线因子）：
- 只挑选 metadata 声明了 ``explanation`` 规格的因子；未声明者跳过；
- 顶层展示顺序按解释规格的 ``order``，同 order 用注册序 tie-break；
- entry 复用因子 metadata 的 logic / data_sources / degradation，不重复书写；
- fields 取自解释规格（可为 output_fields 的策划子集）；
- value / score 从 stock 按声明字段名读取；缺字段取 None（``.get`` 语义）；
- extra 映射按声明顺序追加（如 source / reason），entry 内字段插入序稳定。

真实 7 短线因子的解释块与迁移前逐字节一致性，由
``test_shortline_factors_equivalence.py`` 的 golden 对拍守护。
"""
from __future__ import annotations

from typing import Any, Mapping

from app.services.factors import (
    BaseFactor,
    FactorRegistry,
    build_factor_explanations,
    iter_explainable_factors,
)


class _Explainable(BaseFactor):
    """结构化 dummy：可配 id / output_fields / metadata（含 explanation 规格）。"""

    def __init__(self, id: str, output_fields: tuple[str, ...], metadata: dict) -> None:
        self.id = id
        self.output_fields = tuple(output_fields)
        self.depends_on = ()
        self.metadata = metadata

    def compute(self, stock: Mapping[str, Any], ctx) -> dict:  # pragma: no cover - 解释生成不跑 compute
        return {}


def _reg_two_factors() -> FactorRegistry:
    """构造两个可解释因子 + 一个不可解释因子，注册序故意与 order 相反。"""
    reg = FactorRegistry()
    # 先注册 order=1 的（beta），再注册 order=0 的（alpha）——证明是 order 而非注册序决定展示序。
    reg.register(_Explainable(
        "beta_factor",
        ("beta_score", "beta_level", "beta_extra"),
        {
            "logic": "beta 逻辑",
            "data_sources": ["beta 源"],
            "degradation": "beta 退化",
            "explanation": {
                "key": "beta",
                "order": 1,
                "fields": ["beta_score", "beta_level"],
                "value_field": "beta_level",
                "score_field": "beta_score",
                "extra": {"note": "beta_extra"},
            },
        },
    ))
    reg.register(_Explainable(
        "alpha_factor",
        ("alpha_score", "alpha_level"),
        {
            "logic": "alpha 逻辑",
            "data_sources": ["alpha 源 1", "alpha 源 2"],
            "degradation": "alpha 退化",
            "explanation": {
                "key": "alpha",
                "order": 0,
                "fields": ["alpha_score"],  # 策划子集：只列 score，不列 level
                "value_field": "alpha_level",
                "score_field": "alpha_score",
            },
        },
    ))
    # 无 explanation 规格 —— 应被跳过。
    reg.register(_Explainable(
        "silent_factor",
        ("silent_x",),
        {"logic": "l", "data_sources": ["s"], "degradation": "d"},
    ))
    return reg


def test_iter_explainable_orders_by_order_not_registration():
    reg = _reg_two_factors()
    keys = [spec["key"] for _, spec in iter_explainable_factors(reg)]
    # 只含声明了 explanation 的两个，且按 order 升序（alpha 先于 beta），跳过 silent。
    assert keys == ["alpha", "beta"]


def test_build_reuses_metadata_and_reads_stock_values():
    reg = _reg_two_factors()
    stock = {
        "alpha_score": 88.0,
        "alpha_level": "强",
        "beta_score": 40.0,
        "beta_level": "中",
        "beta_extra": "补充说明",
    }
    exp = build_factor_explanations(stock, reg)

    # 顶层键序按 order。
    assert list(exp.keys()) == ["alpha", "beta"]

    # alpha：fields 为策划子集；logic/data_sources/degradation 复用 metadata；value/score 读 stock。
    assert exp["alpha"] == {
        "fields": ["alpha_score"],
        "logic": "alpha 逻辑",
        "data_sources": ["alpha 源 1", "alpha 源 2"],
        "degradation": "alpha 退化",
        "value": "强",
        "score": 88.0,
    }
    # entry 内字段插入序稳定：fields, logic, data_sources, degradation, value, score。
    assert list(exp["alpha"].keys()) == [
        "fields", "logic", "data_sources", "degradation", "value", "score",
    ]

    # beta：带 extra，追加在 score 之后。
    assert exp["beta"] == {
        "fields": ["beta_score", "beta_level"],
        "logic": "beta 逻辑",
        "data_sources": ["beta 源"],
        "degradation": "beta 退化",
        "value": "中",
        "score": 40.0,
        "note": "补充说明",
    }
    assert list(exp["beta"].keys())[-1] == "note"  # extra 追加在末尾


def test_build_missing_stock_field_yields_none():
    reg = _reg_two_factors()
    # stock 完全缺字段：value/score/extra 均取 None（.get 语义，不报错）。
    exp = build_factor_explanations({}, reg)
    assert exp["alpha"]["value"] is None
    assert exp["alpha"]["score"] is None
    assert exp["beta"]["note"] is None


def test_empty_registry_yields_empty_explanations():
    assert build_factor_explanations({"anything": 1}, FactorRegistry()) == {}
