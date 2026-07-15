from __future__ import annotations

from typing import Any

from app.services.factors import (
    FactorContext,
    build_factor_explanations,
    collect_factor_output_fields,
    default_registry,
    run_factors,
)
# 导入即触发 7 个短线因子注册进 default_registry 并闭环 evaluator 白名单（见该模块底部）。
from app.services.factors.shortline import (
    compute_sector_stats,
    main_industries_of,
    market_sentiment_phase,
)


def apply_shared_shortline_factors(stocks: list[dict[str, Any]], snapshot: dict[str, Any]) -> None:
    """Compute reusable short-line factors once for every strategy plugin.

    薄编排：跨股聚合一次算好 → 建 ``FactorContext`` → ``run_factors`` 按 depends_on
    拓扑序驱动全部已注册短线因子（返回新 stock 列表，不改入参）→ 把因子产出字段写回
    原 stock（保持对外 in-place 契约）→ 由 ``build_factor_explanations`` 依因子 metadata
    声明的解释规格通用拼出 ``shortline_factor_explanations``（不再逐因子硬编码）。

    因子的注册顺序（见 ``factors/shortline.py``）与拓扑排序 tie-break 一起，复刻了迁移前
    ``_apply_*`` 的字段计算顺序；leader 显式 depends_on 其上游，保证读到已 merge 的
    is_main_theme / sector_ladder / capital_resonance 输出。解释块的顶层展示顺序由各
    因子解释规格的 ``order`` 显式声明（与计算顺序解耦），复刻历史输出逐字节一致。
    """
    ctx = FactorContext(
        snapshot=snapshot,
        aggregates={
            "sector_stats": compute_sector_stats(stocks),
            "sentiment_phase": market_sentiment_phase(snapshot),
            "main_industries": main_industries_of(snapshot),
        },
    )
    results = run_factors(stocks, ctx)
    produced_fields = collect_factor_output_fields(default_registry)
    for stock, result in zip(stocks, results):
        # 只写回因子声明的 output_fields（in-place），与迁移前逐字段等价。
        stock.update({field: result[field] for field in produced_fields if field in result})
        # 解释由 metadata 通用生成：从已写回因子字段的 stock 取 value/score/extra。
        stock["shortline_factor_explanations"] = build_factor_explanations(stock)
