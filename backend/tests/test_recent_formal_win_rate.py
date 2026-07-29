"""近 N 个已结算交易日 formal 推荐聚合胜率。

因果：只读 recommendation_runs(已完成 formal) + recommendations(rec_) + recommendation_outcomes(已结算)，
按交易日取最近 N 个有 success/failed outcome 的日期聚合；零推荐和 pending 日期不占窗口。
去污染字段：pure 低线 + pure soft@震荡 的 clean_net_profit_loss_ratio / pollution_n（主口径不变）。
"""
from datetime import datetime

from app.services.outcome_service import (
    _net_pl_ratio,
    _pure_pollution_keys_from_rows,
    invalidate_outcome_metrics_cache,
    recent_formal_win_rate,
)
from app.models.tables import Recommendation, RecommendationOutcome, RecommendationRun

EXPECTED_KEYS = {
    "window_trade_days",
    "trade_dates",
    "sample_count",
    "success_count",
    "win_rate",
    "avg_return",
    "profit_loss_ratio",
    "clean_sample_count",
    "clean_net_profit_loss_ratio",
    "pollution_n",
    "pollution_pl_delta",
}


def _add_formal_recommendation(db, *, trade_date, suffix, outcome_label=None, executed_return=0.0):
    run_id = f"run_recent_window_{suffix}"
    recommendation_id = f"rec_recent_window_{suffix}"
    db.add(RecommendationRun(
        run_id=run_id,
        run_name="近期胜率窗口测试",
        run_mode="formal",
        evaluation_scope="live_forward",
        trade_date=trade_date,
        data_as_of=trade_date,
        source_snapshot_id=f"snapshot_recent_{suffix}",
        status="completed",
    ))
    db.add(Recommendation(
        recommendation_id=recommendation_id,
        run_id=run_id,
        trade_date=trade_date,
        recommended_at=datetime.fromisoformat(f"{trade_date}T06:00:00"),
        code=f"{int(suffix):06d}",
        name="测试股票",
        final_score=80,
    ))
    if outcome_label is not None:
        db.add(RecommendationOutcome(
            recommendation_id=recommendation_id,
            outcome_label=outcome_label,
            outcome_label_legacy=outcome_label,
            executed_return=executed_return,
        ))


def test_recent_formal_win_rate_window_uses_only_settled_trade_dates(db):
    """零推荐日和只有 pending outcome 的日期不得占用近期窗口名额。"""
    _add_formal_recommendation(
        db, trade_date="2099-01-02", suffix="2", outcome_label="success", executed_return=2.0,
    )
    db.add(RecommendationRun(
        run_id="run_recent_window_zero",
        run_name="保护性零推荐",
        run_mode="formal",
        trade_date="2099-01-03",
        status="completed",
    ))
    _add_formal_recommendation(
        db, trade_date="2099-01-04", suffix="4", outcome_label="pending",
    )
    _add_formal_recommendation(
        db, trade_date="2099-01-05", suffix="5", outcome_label="failed", executed_return=-1.0,
    )
    db.flush()
    invalidate_outcome_metrics_cache()

    result = recent_formal_win_rate(db, window_trade_days=2)

    assert result["trade_dates"] == ["2099-01-05", "2099-01-02"]
    assert result["sample_count"] == 2


def test_recent_formal_win_rate_keys_and_shape(db):
    """返回结构完整且字段类型正确。"""
    res = recent_formal_win_rate(db, window_trade_days=5)
    assert isinstance(res, dict)
    assert EXPECTED_KEYS.issubset(res.keys())
    assert res["window_trade_days"] == 5
    assert isinstance(res["trade_dates"], list)
    assert isinstance(res["sample_count"], int)
    assert isinstance(res["success_count"], int)
    assert res["success_count"] <= res["sample_count"]
    assert isinstance(res["pollution_n"], int)
    assert res["pollution_n"] >= 0
    assert isinstance(res["clean_sample_count"], int)
    assert res["clean_sample_count"] <= res["sample_count"]


def test_recent_formal_win_rate_sane_when_data_present(db):
    """有已结算样本时：胜率∈[0,100]、均收益为浮点、胜场不超过样本。"""
    res = recent_formal_win_rate(db, window_trade_days=5)
    if res["sample_count"] == 0:
        # 该窗口无已结算 formal 推荐：诚实返回 None，不虚构
        assert res["win_rate"] is None
        assert res["avg_return"] is None
        return
    assert 0.0 <= res["win_rate"] <= 100.0
    assert isinstance(res["avg_return"], float)
    assert res["success_count"] <= res["sample_count"]
    # 有污染时 clean 样本应更少或相等；无污染时 clean==sample
    if res["pollution_n"] == 0:
        assert res["clean_sample_count"] == res["sample_count"]
        assert res["pollution_pl_delta"] is None
    else:
        assert res["clean_sample_count"] >= 0


def test_recent_formal_win_rate_window_bounds(db):
    """窗口边界不崩溃：单日与最大窗口均返回合法结构。"""
    one = recent_formal_win_rate(db, window_trade_days=1)
    assert EXPECTED_KEYS.issubset(one.keys())
    assert one["window_trade_days"] == 1

    wide = recent_formal_win_rate(db, window_trade_days=60)
    assert EXPECTED_KEYS.issubset(wide.keys())
    assert wide["window_trade_days"] == 60


def test_pure_pollution_keys_low_score_only():
    """仅低线、无 hard 孪生 → pure_low；同日有 hard 孪生 → 不记 pure。"""
    rows = [
        ("A", "2026-07-09", 55.0, None, "震荡"),  # pure low
        ("B", "2026-07-09", 80.0, None, "弱势"),  # hard
        ("C", "2026-07-09", 60.0, None, "弱势"),  # pure low
        ("B", "2026-07-09", 50.0, None, "弱势"),  # 同日 B 有 hard → 不 pure
    ]
    keys = _pure_pollution_keys_from_rows(rows)
    assert ("A", "2026-07-09") in keys
    assert ("C", "2026-07-09") in keys
    assert ("B", "2026-07-09") not in keys


def test_pure_pollution_keys_soft_oscillation():
    """pure soft@震荡 且无 hard 孪生才入污染集。"""
    rows = [
        ("X", "2026-07-16", 75.0, True, "震荡"),  # pure soft@震荡
        ("Y", "2026-07-16", 78.0, True, "弱势"),  # soft 但非震荡 → 不记
        ("Z", "2026-07-16", 80.0, False, "震荡"),  # hard（非 soft）
        ("Z", "2026-07-16", 70.0, True, "震荡"),  # 同日 Z 有 hard → soft 不 pure
    ]
    keys = _pure_pollution_keys_from_rows(rows)
    assert ("X", "2026-07-16") in keys
    assert ("Y", "2026-07-16") not in keys
    assert ("Z", "2026-07-16") not in keys


def test_net_pl_ratio_basic():
    """扣费后盈亏比：赢侧均 / 亏侧均。"""
    # 毛 +5 / -3 → 净 +4.93 / -3.07 → ratio ~1.61
    r = _net_pl_ratio([5.0, -3.0])
    assert r is not None
    assert 1.5 < r < 1.7
    assert _net_pl_ratio([1.0, 2.0]) is None  # 无亏损侧
    assert _net_pl_ratio([]) is None
