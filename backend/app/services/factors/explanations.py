"""因子解释的通用生成（任务 2.4，需求 3.1）。

历史包袱：解释块曾由 ``shortline_factors._attach_factor_explanations`` 对 4 个因子
硬编码拼装，其描述文本（logic / data_sources / degradation）还与因子 metadata 各存
一份——典型 shallow + 漂移风险点。本模块把「解释怎么拼」收成一个 deep function：
遍历注册表中声明了解释规格（``metadata["explanation"]``）的因子，从该因子 metadata
复用描述文本、从 stock 的因子产出值取 value / score / 额外字段，拼出与历史逐字节
一致的 ``shortline_factor_explanations`` dict。

窄接口：对外只有 ``build_factor_explanations``（生成）与 ``iter_explainable_factors``
（枚举可解释因子）。新增 / 改动一个可解释因子，只需在其 metadata 声明解释规格，
无需改动本生成器，也无需再维护第二份描述文本（locality + 消除漂移）。

解释规格（``metadata["explanation"]``）字段：
- ``key``: 解释 dict 的顶层键（如 ``"capital_resonance"``）。
- ``order``: 顶层展示顺序。**与注册 / 拓扑序解耦**——展示顺序是表现层关注点，不应
  被因子的计算顺序牵动；显式声明以复刻历史输出顺序，稳定可复现。
- ``fields``: 解释里列出的字段清单（**策划子集**，未必等于 ``output_fields``，故必须
  显式声明而不能从 ``output_fields`` 推导——sector_ladder / capital_resonance 即只列
  其中两项）。
- ``value_field`` / ``score_field``: 从 stock 读取 value / score 的字段名。
- ``extra``: 可选，``{解释键: stock 字段名}`` 有序映射（如 capital_resonance 的
  ``source``、twenty_cm_risk 的 ``reason``）。

描述文本（logic / data_sources / degradation）一律复用因子 metadata 同名项，不在
解释规格里重复书写。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .registry import FactorRegistry, default_registry

# 因子 metadata 中声明解释规格所用的键。
EXPLANATION_METADATA_KEY = "explanation"


def iter_explainable_factors(
    registry: FactorRegistry = default_registry,
) -> "list[tuple[Any, Mapping[str, Any]]]":
    """按展示 ``order`` 返回 ``(factor, explanation_spec)`` 列表。

    只挑选 metadata 声明了 ``explanation`` 规格的因子。``order`` 缺省者排到末尾，
    同 ``order`` 再以注册序 tie-break，保证输出稳定可复现。
    """
    items: "list[tuple[int, int, Any, Mapping[str, Any]]]" = []
    fallback_base = len(registry)
    for idx, factor in enumerate(registry.list_factors()):
        meta = getattr(factor, "metadata", None) or {}
        spec = meta.get(EXPLANATION_METADATA_KEY)
        if not spec:
            continue
        order = spec.get("order", fallback_base + idx)
        items.append((order, idx, factor, spec))
    items.sort(key=lambda item: (item[0], item[1]))
    return [(factor, spec) for _, _, factor, spec in items]


def build_factor_explanations(
    stock: Mapping[str, Any],
    registry: FactorRegistry = default_registry,
) -> "dict[str, dict[str, Any]]":
    """从一只已跑过因子的 ``stock`` 通用拼出 ``shortline_factor_explanations``。

    对注册表中每个声明了解释规格的因子：复用其 metadata 的 logic / data_sources /
    degradation，配上解释规格声明的 fields，再从 ``stock`` 取 value / score 与可选
    extra 字段，产出一个解释 entry。顶层键序（按 ``order``）与 entry 内字段序均复刻
    历史 ``_attach_factor_explanations`` 的输出。

    读取用 ``stock.get`` —— 与历史实现一致，缺字段时取 ``None`` 而非报错。
    """
    explanations: "dict[str, dict[str, Any]]" = {}
    for factor, spec in iter_explainable_factors(registry):
        meta = factor.metadata
        # entry 字段插入序复刻历史：fields, logic, data_sources, degradation, value,
        # score, 然后是可选 extra（source / reason）。
        entry: "dict[str, Any]" = {
            "fields": spec["fields"],
            "logic": meta["logic"],
            "data_sources": meta["data_sources"],
            "degradation": meta["degradation"],
            "value": stock.get(spec["value_field"]),
            "score": stock.get(spec["score_field"]),
        }
        for out_key, src_field in (spec.get("extra") or {}).items():
            entry[out_key] = stock.get(src_field)
        explanations[spec["key"]] = entry
    return explanations
