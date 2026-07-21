"""短线因子迁移「等价对拍」测试（任务 2.3，需求 3.5 / 1.1）。

核心验收：把 6 个 ``_apply_*_factor`` 迁移进因子框架（含新增 market_context，共 7 个
Factor）后，``apply_shared_shortline_factors`` 在**相同输入**下输出的每个字段（含
``shortline_factor_explanations``）必须与迁移**前**逐字节一致。

对拍方式（golden-master）：
- ``tests/data/shortline_factor_golden.json`` 是在迁移前用现状代码跑代表性场景冻结的
  golden（见 ``_shortline_equiv_fixtures`` 与一次性捕获脚本）；
- 本测试用**同一批**场景（``build_scenarios``）跑迁移后的实现，逐股票逐字段与 golden 比对。

纯 in-process：``apply_shared_shortline_factors`` 只依赖 ``board_rules`` 与因子框架，
不吃 DB / 网络 / pandas，本环境可直接运行（无需 production_mysql 标注）。

附带守护：
- 跨股聚合构造器（板块统计 / 四相情绪 / 主线集合）单元行为；
- 各因子 ``compute`` 只返回自己的 output_fields、不原地改 stock；
- ``apply_shared_shortline_factors`` 的 in-place 对外契约（返回 None、就地写回原 dict）；
- 白名单闭环：7 因子的 output_fields 已注入 ``evaluator.ALLOWED_NAMES``（需求 3.2）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from _shortline_equiv_fixtures import (
    EXPLANATIONS_FIELD,
    FACTOR_OUTPUT_FIELDS,
    build_scenarios,
    produced_view,
)
from app.engine import evaluator
from app.services.factors import (
    FactorContext,
    bootstrap_default_factor_registry,
    default_registry,
    run_factors,
)
from app.services.factors.shortline import (
    CapitalResonanceFactor,
    DragonTigerFactor,
    LeaderFactor,
    LimitUpOverheatFactor,
    MarketContextFactor,
    SectorLadderFactor,
    TwentyCmRiskFactor,
    compute_sector_stats,
    main_industries_of,
    market_sentiment_phase,
    sector_key,
)
from app.services.shortline_factors import apply_shared_shortline_factors

GOLDEN_PATH = Path(__file__).resolve().parent / "data" / "shortline_factor_golden.json"


@pytest.fixture(scope="module", autouse=True)
def _bootstrap_shortline_factors():
    """apply_shared_shortline_factors 依赖 default_registry 已 bootstrap 并冻结。"""
    bootstrap_default_factor_registry()


def _load_golden() -> dict[str, list[dict]]:
    assert GOLDEN_PATH.exists(), (
        f"缺少 golden 快照 {GOLDEN_PATH}；应在迁移前用现状代码捕获后提交。"
    )
    raw = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    return {scenario["name"]: scenario["stocks"] for scenario in raw}


# ── 核心：逐字段等价对拍 ────────────────────────────────────────────────
@pytest.mark.parametrize("scenario", build_scenarios(), ids=lambda s: s["name"])
def test_apply_shared_shortline_factors_matches_golden(scenario):
    golden_by_name = _load_golden()
    golden_stocks = golden_by_name[scenario["name"]]

    stocks = [dict(s) for s in scenario["stocks"]]
    apply_shared_shortline_factors(stocks, scenario["snapshot"])

    assert len(stocks) == len(golden_stocks)
    for idx, (stock, golden) in enumerate(zip(stocks, golden_stocks)):
        got = produced_view(stock)
        # 先逐个 output_field 精确比对，失败信息精确到「场景/第几只/哪个字段」。
        for field in FACTOR_OUTPUT_FIELDS:
            assert got[field] == golden[field], (
                f"[{scenario['name']}] 第 {idx} 只（code={stock.get('code')}）字段 {field} 不一致："
                f"迁移后={got[field]!r} vs golden={golden[field]!r}"
            )
        # 解释块整体逐字节一致（含 metadata / value / score / source / reason）。
        assert got[EXPLANATIONS_FIELD] == golden[EXPLANATIONS_FIELD], (
            f"[{scenario['name']}] 第 {idx} 只（code={stock.get('code')}）"
            f"{EXPLANATIONS_FIELD} 与 golden 不一致"
        )


def test_every_golden_scenario_is_exercised():
    """防御：golden 里的每个场景都被 build_scenarios 覆盖，避免遗漏场景导致漏测。"""
    golden_names = set(_load_golden())
    scenario_names = {s["name"] for s in build_scenarios()}
    assert golden_names == scenario_names


# ── in-place 对外契约（feature_builder 依赖此契约，不能破坏）──────────────
def test_apply_is_in_place_and_returns_none():
    stocks = [dict(s) for s in build_scenarios()[0]["stocks"]]
    snapshot = build_scenarios()[0]["snapshot"]
    same_refs = [id(s) for s in stocks]

    result = apply_shared_shortline_factors(stocks, snapshot)

    assert result is None  # 对外仍是 in-place（返回 None）
    assert [id(s) for s in stocks] == same_refs  # 写回的是原对象，不替换列表元素
    for stock in stocks:
        # 因子字段确已就地写入原 dict
        assert "shortline_leader_score" in stock
        assert "is_main_theme" in stock
        assert EXPLANATIONS_FIELD in stock


# ── 跨股聚合构造器 ─────────────────────────────────────────────────────
def test_market_sentiment_phase_all_four():
    assert market_sentiment_phase({"sample_count": 300, "limit_up_count": 12, "limit_down_count": 2}) == "高潮"
    # 活跃：涨停率>0.5% 且 lu/ld ≥ 1.2（T28 双维度）
    assert market_sentiment_phase({"sample_count": 1000, "limit_up_count": 8, "limit_down_count": 4}) == "活跃"
    assert market_sentiment_phase({"sample_count": 1000, "limit_up_count": 3, "limit_down_count": 1}) == "平淡"
    assert market_sentiment_phase({"sample_count": 1000, "limit_up_count": 0, "limit_down_count": 5}) == "冰点"
    # 伪活跃：涨停率够但跌停更多 → 平淡
    assert market_sentiment_phase({"sample_count": 1000, "limit_up_count": 8, "limit_down_count": 10}) == "平淡"


def test_main_industries_of_strips_and_stringifies():
    assert main_industries_of({"main_industries": ["机器人", "  ", "半导体", ""]}) == {"机器人", "半导体"}
    assert main_industries_of({}) == set()


def test_sector_key_prefers_concept_then_industry():
    assert sector_key({"concept_sector_name": "机器人", "industry": "电子"}) == "机器人"
    assert sector_key({"industry": "电子"}) == "电子"
    assert sector_key({}) == "未分类"


def test_compute_sector_stats_counts_ladder():
    stocks = [
        {"concept_sector_name": "机器人", "is_limit_up": True, "limit_up_consecutive_days": 1, "pct": 10, "concept_sector_heat_rank": 3},
        {"concept_sector_name": "机器人", "is_limit_up": True, "limit_up_consecutive_days": 2, "pct": 10, "main_net_inflow_yi": 1.0},
        {"concept_sector_name": "机器人", "is_limit_up": True, "limit_up_consecutive_days": 4, "pct": 10, "main_net_inflow_yi": 0.5},
        {"concept_sector_name": "机器人", "pct": 3.5},
    ]
    stats = compute_sector_stats(stocks)["机器人"]
    assert stats["stock_count"] == 4
    assert stats["limit_up_count"] == 3
    assert stats["first_board_count"] == 1
    assert stats["second_board_count"] == 1
    assert stats["high_board_count"] == 1
    assert stats["height"] == 4
    assert stats["strong_count"] == 4  # 四只 pct>=3
    assert stats["positive_flow_count"] == 2
    assert stats["best_heat_rank"] == 3


# ── 因子 compute 的窄契约：只返回自己的 output_fields、不原地改 stock ──────
FACTORS = [
    MarketContextFactor,
    SectorLadderFactor,
    CapitalResonanceFactor,
    LimitUpOverheatFactor,
    TwentyCmRiskFactor,
    LeaderFactor,
    DragonTigerFactor,
]


@pytest.mark.parametrize("factor_cls", FACTORS, ids=lambda c: c.id)
def test_compute_returns_only_declared_fields(factor_cls):
    factor = factor_cls()
    stock = {
        "code": "300001",
        "industry": "机器人",
        "concept_sector_name": "机器人",
        "pct": 12.0,
        "is_limit_up": True,
        "dragon_tiger_is_on_list": True,
        "dragon_tiger_buy_seat_count": 1,
        "dragon_tiger_net_amount_yi": 1.0,
        "dragon_tiger_institution_net_buy_yi": 0.5,
    }
    ctx = FactorContext(
        snapshot={"sample_count": 300, "limit_up_count": 12, "limit_down_count": 2},
        aggregates={
            "sector_stats": compute_sector_stats([stock]),
            "sentiment_phase": "高潮",
            "main_industries": {"机器人"},
        },
    )
    produced = factor.compute(stock, ctx)
    assert set(produced).issubset(set(factor.output_fields))
    assert "code" in stock and len(stock) >= 8  # 入参未被增删字段（compute 不原地改）


def test_leader_depends_on_upstream_factors():
    """leader 依赖三上游因子，保证读到已 merge 的 is_main_theme/板块/资金分。"""
    assert set(LeaderFactor.depends_on) == {"market_context", "sector_ladder", "capital_resonance"}


def test_run_factors_default_registry_is_topologically_orderable():
    """默认注册表（7 短线因子）可解析出拓扑序，且 leader 在其三上游之后。"""
    plan = bootstrap_default_factor_registry()
    assert default_registry.is_frozen is True
    assert plan.factor_ids
    order = [f.id for f in default_registry.resolve_order()]
    assert set(order) >= {
        "market_context", "sector_ladder", "capital_resonance",
        "limit_up_overheat", "twenty_cm_risk", "leader", "dragon_tiger",
    }
    assert order.index("market_context") < order.index("leader")
    assert order.index("sector_ladder") < order.index("leader")
    assert order.index("capital_resonance") < order.index("leader")


# ── 白名单闭环（需求 3.2）：7 因子 output_fields 已注入 evaluator.ALLOWED_NAMES ──
def test_factor_output_fields_injected_into_evaluator_allowlist():
    # 导入 factors.shortline 时已 bootstrap 注入；此处断言全部 output_fields 在白名单内。
    missing = [f for f in FACTOR_OUTPUT_FIELDS if f not in evaluator.ALLOWED_NAMES]
    assert not missing, f"以下因子 output_fields 未进入 evaluator 白名单：{missing}"
    # 这两个字段迁移前并不在硬编码白名单里，能出现即证明是注入闭环补上的。
    assert "capital_resonance_source" in evaluator.ALLOWED_NAMES
    assert "twenty_cm_risk_reason" in evaluator.ALLOWED_NAMES
