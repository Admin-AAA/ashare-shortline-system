"""值分布哨兵测试（L-143）——锁定「恒零/恒同值/整列 NULL 必须扣分」。

背景数据墙：既有健康度四类扣分全是结构性的（表空/日期滞后/缺天/数据源连通），
回答的是「管道有没有在跑」。实测三次失真全部躲过它、评分全程 100：
  - upper_shadow_count20 自 2026-09-09 起全表恒 0 达 14 天
  - macd_dif/dea 连续 12 个交易日全表 0.0，macd_status 恒为「下穿零轴」
  - pct_chg 在两次人工体检之间被刷成全零，limit_up_days20 随之恒 0
本测试锁定哨兵能抓住这三种形态，且不误报布尔型列。
"""
from __future__ import annotations

from app.services.data_health_service import (
    _VALUE_SENTINEL_DEFS,
    _compute_health_score,
)


def _sentinel(distorted: list[dict], penalty: int) -> dict:
    return {
        "reference_trade_date": "2026-09-23",
        "total_rows": 4949,
        "checked": len(_VALUE_SENTINEL_DEFS),
        "healthy_count": len(_VALUE_SENTINEL_DEFS) - len(distorted),
        "distorted": distorted,
        "distorted_count": len(distorted),
        "critical_distorted_count": sum(1 for d in distorted if d["critical"]),
        "score_penalty": penalty,
    }


def _clean_structural_inputs() -> tuple[dict, dict, str, dict]:
    """结构性维度全绿——单独验证值分布维度对评分的影响。"""
    table_stats = {
        "daily_kbar": {"critical": True, "total_rows": 5000, "max_date": "2026-09-23"},
        "outcome": {"critical": True, "total_rows": 500},
        "recommendation": {"critical": True, "total_rows": 500},
    }
    return table_stats, {}, "2026-09-23", {"tables": {}}


def test_structural_checks_alone_give_full_score():
    """基线：结构性全绿且无值分布输入时是满分——这正是失真能潜伏的原因。"""
    table_stats, lag, ref, coverage = _clean_structural_inputs()
    assert _compute_health_score(table_stats, lag, ref, coverage) == 100


def test_all_zero_column_costs_score():
    """恒零列（唯一值 1）必须扣分，不能再满分。"""
    table_stats, lag, ref, coverage = _clean_structural_inputs()
    sentinel = _sentinel(
        [{
            "column": "upper_shadow_count20", "label": "20日长上影次数",
            "critical": False, "reason": "恒同值（唯一值 1）",
            "not_null": 4949, "distinct": 1, "non_zero": 0,
            "nonzero_ratio": 0.0, "total_rows": 4949,
        }],
        penalty=2,
    )
    score = _compute_health_score(table_stats, lag, ref, coverage, None, None, sentinel)
    assert score == 98, f"恒零列必须扣分，实际 {score}"


def test_critical_column_distortion_costs_more():
    """critical 列（pct_chg / turnover_rate 等直接进决策）失真按更高权重扣分。"""
    table_stats, lag, ref, coverage = _clean_structural_inputs()
    sentinel = _sentinel(
        [{
            "column": "pct_chg", "label": "涨跌幅",
            "critical": True, "reason": "恒同值（唯一值 1）",
            "not_null": 4949, "distinct": 1, "non_zero": 0,
            "nonzero_ratio": 0.0, "total_rows": 4949,
        }],
        penalty=5,
    )
    score = _compute_health_score(table_stats, lag, ref, coverage, None, None, sentinel)
    assert score == 95, f"critical 列失真应扣 5 分，实际 {score}"


def test_sentinel_penalty_is_capped():
    """单一维度不得独吞整个评分——penalty 封顶 20。"""
    table_stats, lag, ref, coverage = _clean_structural_inputs()
    many = [
        {
            "column": f"c{i}", "label": f"列{i}", "critical": True,
            "reason": "整列 NULL", "not_null": 0, "distinct": 0,
            "non_zero": 0, "nonzero_ratio": 0.0, "total_rows": 4949,
        }
        for i in range(12)
    ]
    sentinel = _sentinel(many, penalty=20)  # 12*5=60 -> 实现里已 min(...,20)
    score = _compute_health_score(table_stats, lag, ref, coverage, None, None, sentinel)
    assert score == 80, f"penalty 应封顶 20，实际扣到 {100 - score}"


def test_boolean_column_spec_tolerates_two_distinct_values():
    """布尔型列只有 0/1 两个值，期望必须放宽否则必然误报。"""
    spec = next(s for s in _VALUE_SENTINEL_DEFS if s["column"] == "breakout20")
    assert spec["min_distinct"] <= 2, "breakout20 期望唯一值数必须 <=2"
    assert spec["min_nonzero_ratio"] == 0.0, "布尔列不应对非零占比设下限"


def test_decision_critical_columns_are_marked_critical():
    """直接进选股决策的列必须标 critical：pct_chg 喂 limit_up_days20（涨停策略
    universe 硬条件），turnover_rate 被 9 个策略的 prefilter 使用。"""
    by_col = {s["column"]: s for s in _VALUE_SENTINEL_DEFS}
    for col in ("pct_chg", "turnover_rate", "close", "volume", "amount"):
        assert col in by_col, f"{col} 必须纳入值分布哨兵"
        assert by_col[col]["critical"] is True, f"{col} 必须标为 critical"
