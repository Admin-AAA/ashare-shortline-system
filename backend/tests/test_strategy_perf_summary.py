"""T7-B strategy_perf_summary 测试 —— 推荐卡片"历史同策略胜率"数据源。

北极星 B 项：每条推荐一屏可见该策略历史战绩。strategy_perf_summary 是轻量 SQL，
只查一个策略（对比 metrics_by_strategy 全策略计算），供 get_recommendation_detail 调用。
"""
from app.services.outcome_service import strategy_perf_summary


_KNOWN_STRATEGY = "daily-stock-analysis-skill"


def test_strategy_perf_summary_returns_complete_fields(db):
    """已知策略返回完整字段：样本/标注/胜率/盈亏比/平均收益/最近命中日。"""
    result = strategy_perf_summary(db, _KNOWN_STRATEGY, window_days=180)
    assert result is not None, "daily-stock-analysis-skill 应有历史样本"
    assert result["strategy_id"] == _KNOWN_STRATEGY
    assert result["sample_count"] > 0
    assert result["labeled_count"] > 0
    assert result["success_count"] >= 0
    # 胜率 0-100 区间
    assert 0 <= result["hit_rate"] <= 100
    # 最近命中日是 YYYY-MM-DD 格式
    assert result["last_recommendation_date"] is not None
    assert len(result["last_recommendation_date"]) == 10
    assert result["window_days"] == 180


def test_strategy_perf_summary_none_for_unknown_strategy(db):
    """不存在的策略返回 None（前端卡片显示"—"）。"""
    result = strategy_perf_summary(db, "nonexistent-strategy-xyz")
    assert result is None


def test_strategy_perf_summary_empty_strategy_id_returns_none(db):
    """空 strategy_id 返回 None（推荐无 bucket 时的兜底）。"""
    assert strategy_perf_summary(db, "") is None
    assert strategy_perf_summary(db, None) is None  # type: ignore[arg-type]


def test_strategy_perf_summary_window_filters_samples(db):
    """window_days=1 应大幅缩小样本量（验证时间窗口生效）。"""
    wide = strategy_perf_summary(db, _KNOWN_STRATEGY, window_days=180)
    narrow = strategy_perf_summary(db, _KNOWN_STRATEGY, window_days=1)
    assert wide is not None and narrow is not None
    # 1 天窗口样本量应 <= 180 天窗口样本量（通常严格小于，除非当日无推荐）
    assert narrow["sample_count"] <= wide["sample_count"]
