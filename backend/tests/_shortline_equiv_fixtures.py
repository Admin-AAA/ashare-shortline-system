"""共享夹具：短线因子迁移「等价对拍」的代表性输入 + 输出字段清单。

本模块**不 import 任何 app 代码**，只产出纯数据（stocks/snapshot），因此可同时被：
- 迁移「前」的 golden 捕获脚本（跑旧 ``apply_shared_shortline_factors``）；
- 迁移「后」的等价对拍测试（跑新 ``apply_shared_shortline_factors``）；

复用同一批输入，保证对拍的是「同输入 → 同输出」而非两套数据。文件名带前导下划线
且非 ``test_`` 前缀，pytest 不会将其当作用例收集。

场景覆盖（见 build_scenarios）：
- market_sentiment_phase 四相：高潮 / 活跃 / 平淡 / 冰点（由 snapshot 涨停率决定）；
- sector_ladder：未分类（缺失分支）、正常板块梯队（首/二/高板、强势股、热度排名）、多档 level；
- capital_resonance：有 capital_flow（capital_flow_checked / 不在 missing 两种就绪）与
  无 capital_flow 回退（price_volume_fallback）、净流入正负、各分档；
- twenty_cm_risk：创业(300/301)、科创(688/689)、北交(8/4/BJ)、主板非 20cm、缺板块信息；
  20cm 标的 block / caution / pass 各闸门；
- leader：is_main_theme、板块/资金分、涨停或涨幅、突破 20/60、dist60 阈值 → 候选真/假；
- dragon_tiger：未上榜、上榜席位集中/一般/分散、净买额为 0 与正负比例；
- market_context：主题命中（industry / concept_sector_name）、未分类强制非主线、未命中。
"""
from __future__ import annotations

from typing import Any


# 迁移后 7 个因子（含 market_context）声明的全部 output_fields，等价对拍逐字段比对以此为准。
FACTOR_OUTPUT_FIELDS: tuple[str, ...] = (
    # market_context
    "is_main_theme",
    "market_sentiment_phase",
    # sector_ladder
    "sector_ladder_completeness_score",
    "sector_ladder_level",
    "sector_ladder_limit_up_count",
    "sector_ladder_height",
    # capital_resonance
    "capital_resonance_score",
    "capital_resonance_signal",
    "capital_resonance_source",
    # limit_up_overheat
    "limit_up_overheat_score",
    "limit_up_overheat_level",
    # twenty_cm_risk
    "is_20cm_stock",
    "twenty_cm_risk_score",
    "twenty_cm_risk_gate",
    "twenty_cm_risk_reason",
    # leader
    "shortline_leader_score",
    "is_shortline_leader_candidate",
    # dragon_tiger
    "dragon_tiger_buy_dispersion",
    "dragon_tiger_inst_buy_ratio",
)

# 解释字段单独列出：由 factors.explanations.build_factor_explanations 依 metadata 通用生成
# （任务 2.4，取代旧硬编码拼装），须与迁移前逐字不变。
EXPLANATIONS_FIELD = "shortline_factor_explanations"


def produced_view(stock: dict[str, Any]) -> dict[str, Any]:
    """从一只已跑过因子的 stock 中，抽取「因子产出字段 + 解释」这一对拍视图。"""
    view = {field: stock.get(field) for field in FACTOR_OUTPUT_FIELDS}
    view[EXPLANATIONS_FIELD] = stock.get(EXPLANATIONS_FIELD)
    return view


def _robot(code: str, **overrides: Any) -> dict[str, Any]:
    """机器人板块基础票（concept_sector_name=机器人），供搭建同板块梯队聚合。"""
    base: dict[str, Any] = {
        "code": code,
        "name": f"机器人{code}",
        "industry": "机器人",
        "concept_sector_name": "机器人",
        "concept_sector_heat_rank": 3,
        "pct": 3.5,
        "close": 20.0,
        "price": 20.0,
        "amount_yi": 6.0,
        "turnover_rate": 8.0,
        "vol5_20": 1.4,
        "main_net_inflow_yi": 1.2,
        "main_net_inflow_ratio": 9.0,
        "super_large_net_inflow_yi": 0.6,
        "large_net_inflow_yi": 0.3,
        "small_net_inflow_yi": -0.4,
        "sector_fund_rank": 3,
        "breakout20": True,
        "breakout60": True,
        "dist60": 12.0,
        "pos60": 80.0,
        "data_quality": {"capital_flow_checked": True, "missing_fields": []},
    }
    base.update(overrides)
    return base


def _scenario_high() -> dict[str, Any]:
    """高潮场景：机器人板块完整梯队 + 20cm 各板 + 龙虎榜多态。

    snapshot: sample=300 limit_up=12 limit_down=2 → lu_rate=0.04（高潮，且过热市场档最高）。
    main_industries=[机器人] → 板块内主题命中。
    """
    stocks = [
        # 创业 20cm 首板、主线、强资金、突破、高位（触发多处 True）
        _robot(
            "300001",
            pct=13.0,
            is_limit_up=True,
            limit_up_consecutive_days=1,
            turnover_rate=22.0,
            amount_yi=2.0,
            pos60=98.0,
            dist60=35.0,
            limit_up_open_times=9,
            concept_sector_heat_rank=2,
            dragon_tiger_is_on_list=True,
            dragon_tiger_buy_seat_count=1,
            dragon_tiger_net_amount_yi=1.2,
            dragon_tiger_institution_net_buy_yi=1.2,
        ),
        # 创业 20cm 二板
        _robot(
            "300002",
            pct=10.0,
            is_limit_up=True,
            limit_up_consecutive_days=2,
            dragon_tiger_is_on_list=True,
            dragon_tiger_buy_seat_count=4,
            dragon_tiger_net_amount_yi=2.0,
            dragon_tiger_institution_net_buy_yi=1.0,
        ),
        # 科创 20cm 高板（4 连板）
        _robot(
            "688001",
            pct=10.0,
            is_limit_up=True,
            limit_up_consecutive_days=4,
            turnover_rate=19.0,
            amount_yi=8.0,
        ),
        # 创业 20cm 5 连板龙头：触发 limit_up_overheat「高」档（连板 30 + 涨停 14 + 位置 12 + 开板 8 + 市场 28）
        _robot(
            "300003",
            pct=10.0,
            is_limit_up=True,
            limit_up_consecutive_days=5,
            pos60=98.0,
            dist60=35.0,
            limit_up_open_times=10,
        ),
        # 主板非 20cm 强势股（pct>=3 计入 strong_count，但未涨停）
        _robot("600100", pct=3.5, dragon_tiger_is_on_list=False),
        # 同板块弱票：无 capital_flow → 资金回退路径；净流入为负
        _robot(
            "000200",
            pct=1.0,
            main_net_inflow_yi=-0.5,
            data_quality={"missing_fields": ["capital_flow"]},
            dragon_tiger_is_on_list=True,
            dragon_tiger_buy_seat_count=6,
            dragon_tiger_net_amount_yi=0.0,
            dragon_tiger_institution_net_buy_yi=0.5,
        ),
        # 未分类板块 + 北交所（20cm 30% 档）+ 非主线（industry 未分类强制 False）
        {
            "code": "830001",
            "name": "北交未分类",
            "industry": "未分类",
            "exchange": "BJ",
            "pct": 6.0,
            "close": 15.0,
            "price": 15.0,
            "amount_yi": 1.5,
            "turnover_rate": 12.0,
            "vol5_20": 1.1,
            "dragon_tiger_is_on_list": True,
            "dragon_tiger_buy_seat_count": 3,
            "dragon_tiger_net_amount_yi": 1.0,
            "dragon_tiger_institution_net_buy_yi": 0.5,
            "data_quality": {"missing_fields": ["capital_flow"]},
        },
    ]
    snapshot = {
        "sample_count": 300,
        "limit_up_count": 12,
        "limit_down_count": 2,
        "main_industries": ["机器人"],
        "trade_date": "2026-05-12",
    }
    return {"name": "high_sentiment_robot", "stocks": stocks, "snapshot": snapshot}


def _scenario_cold() -> dict[str, Any]:
    """冰点场景：无涨停；覆盖非主线、未分类、缺板块信息、龙虎榜未上榜/分散等。

    snapshot: sample=1000 limit_up=0 limit_down=5 → lu_rate=0（冰点，过热市场档=0）。
    main_industries=[半导体]。
    """
    stocks = [
        # 白酒（不在 main_industries）→ 非主线；龙虎榜一般（席位=3），净买比例 0.5
        {
            "code": "600519",
            "name": "贵州茅台",
            "industry": "白酒",
            "pct": -1.2,
            "close": 1600.0,
            "price": 1600.0,
            "amount_yi": 30.0,
            "turnover_rate": 1.5,
            "vol5_20": 0.9,
            "main_net_inflow_yi": 0.8,
            "main_net_inflow_ratio": 3.0,
            "super_large_net_inflow_yi": 0.2,
            "large_net_inflow_yi": 0.1,
            "small_net_inflow_yi": 0.3,
            "sector_fund_rank": 20,
            "data_quality": {"capital_flow_checked": True, "missing_fields": []},
            "dragon_tiger_is_on_list": True,
            "dragon_tiger_buy_seat_count": 3,
            "dragon_tiger_net_amount_yi": 2.0,
            "dragon_tiger_institution_net_buy_yi": 1.0,
        },
        # 未分类 + 龙虎榜分散（席位=6）+ 净买额为 0 → 比例 0.0（除零分支）
        {
            "code": "000636",
            "name": "未分类票",
            "industry": "未分类",
            "pct": 0.5,
            "close": 8.0,
            "price": 8.0,
            "amount_yi": 2.0,
            "turnover_rate": 3.0,
            "vol5_20": 1.0,
            "data_quality": {"missing_fields": ["capital_flow"]},
            "dragon_tiger_is_on_list": True,
            "dragon_tiger_buy_seat_count": 6,
            "dragon_tiger_net_amount_yi": 0.0,
            "dragon_tiger_institution_net_buy_yi": 0.4,
        },
        # 半导体（在 main_industries）→ 主线；创业 20cm；未涨停但 pct=5（leader pct>=5 True）
        {
            "code": "300500",
            "name": "半导体创业",
            "industry": "半导体",
            "concept_sector_name": "半导体",
            "concept_sector_heat_rank": 1,
            "pct": 5.0,
            "close": 40.0,
            "price": 40.0,
            "amount_yi": 10.0,
            "turnover_rate": 10.0,
            "vol5_20": 1.6,
            "main_net_inflow_yi": 2.5,
            "main_net_inflow_ratio": 12.0,
            "super_large_net_inflow_yi": 1.0,
            "large_net_inflow_yi": 0.5,
            "small_net_inflow_yi": -0.6,
            "sector_fund_rank": 2,
            "breakout20": True,
            "breakout60": True,
            "dist60": 15.0,
            "pos60": 85.0,
            "data_quality": {"capital_flow_checked": True, "missing_fields": []},
            "dragon_tiger_is_on_list": False,
        },
        # 科创 20cm 高换手高位（block 闸门候选）
        {
            "code": "688888",
            "name": "科创高波动",
            "industry": "半导体",
            "concept_sector_name": "半导体",
            "concept_sector_heat_rank": 4,
            "pct": 16.0,
            "is_limit_up": True,
            "close": 60.0,
            "price": 60.0,
            "amount_yi": 2.5,
            "turnover_rate": 25.0,
            "vol5_20": 2.0,
            "pos60": 98.0,
            "dist60": 40.0,
            "data_quality": {"capital_flow_checked": True, "missing_fields": []},
            "dragon_tiger_is_on_list": False,
        },
        # 缺板块信息（code 不匹配前缀、无 exchange/market）→ is_twenty_cm「缺少板块信息」
        {
            "code": "999999",
            "name": "无板块信息",
            "industry": "半导体",
            "concept_sector_name": "半导体",
            "pct": 2.0,
            "close": 10.0,
            "price": 10.0,
            "amount_yi": 4.0,
            "turnover_rate": 5.0,
            "vol5_20": 1.0,
            "exchange": "",
            "market": "",
            "data_quality": {"missing_fields": ["capital_flow"]},
            "dragon_tiger_is_on_list": False,
        },
    ]
    snapshot = {
        "sample_count": 1000,
        "limit_up_count": 0,
        "limit_down_count": 5,
        "main_industries": ["半导体"],
        "trade_date": "2026-05-13",
    }
    return {"name": "cold_sentiment", "stocks": stocks, "snapshot": snapshot}


def _scenario_active() -> dict[str, Any]:
    """活跃场景：lu_rate=0.008（过热市场档中档），验证 sentiment=活跃 与主题命中（concept 命中）。"""
    stocks = [
        {
            "code": "002001",
            "name": "concept命中",
            "industry": "其他",
            "concept_sector_name": "光伏",  # 命中 main_industries（走 concept 路径）
            "concept_sector_heat_rank": 8,
            "pct": 6.0,
            "is_limit_up": True,
            "limit_up_consecutive_days": 3,
            "close": 12.0,
            "price": 12.0,
            "amount_yi": 5.0,
            "turnover_rate": 9.0,
            "vol5_20": 1.5,
            "main_net_inflow_yi": 0.3,
            "main_net_inflow_ratio": 4.0,
            "super_large_net_inflow_yi": 0.1,
            "large_net_inflow_yi": -0.05,
            "small_net_inflow_yi": 0.1,
            "sector_fund_rank": 12,
            "breakout20": True,
            "breakout60": False,
            "dist60": 5.0,
            "pos60": 70.0,
            "data_quality": {"capital_flow_checked": True, "missing_fields": []},
            "dragon_tiger_is_on_list": False,
        },
    ]
    snapshot = {
        "sample_count": 1000,
        "limit_up_count": 8,
        "limit_down_count": 10,
        "main_industries": ["光伏"],
        "trade_date": "2026-05-14",
    }
    return {"name": "active_sentiment", "stocks": stocks, "snapshot": snapshot}


def _scenario_flat() -> dict[str, Any]:
    """平淡场景：lu_rate=0.003（过热市场档最低非零），验证 sentiment=平淡。"""
    stocks = [
        {
            "code": "002888",
            "name": "平淡票",
            "industry": "钢铁",
            "concept_sector_name": "钢铁",
            "concept_sector_heat_rank": 30,
            "pct": 2.0,
            "close": 5.0,
            "price": 5.0,
            "amount_yi": 3.0,
            "turnover_rate": 4.0,
            "vol5_20": 1.0,
            "main_net_inflow_yi": 0.05,
            "main_net_inflow_ratio": 1.0,
            "sector_fund_rank": 40,
            "data_quality": {"missing_fields": ["capital_flow"]},
            "dragon_tiger_is_on_list": False,
        },
    ]
    snapshot = {
        "sample_count": 1000,
        "limit_up_count": 3,
        "limit_down_count": 1,
        "main_industries": ["半导体"],
        "trade_date": "2026-05-15",
    }
    return {"name": "flat_sentiment", "stocks": stocks, "snapshot": snapshot}


def build_scenarios() -> list[dict[str, Any]]:
    """返回全部对拍场景。每个场景 = {name, stocks, snapshot}。"""
    return [
        _scenario_high(),
        _scenario_cold(),
        _scenario_active(),
        _scenario_flat(),
    ]
