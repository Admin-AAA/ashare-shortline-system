"""近 N 交易日 formal 推荐聚合胜率 —— 驾驶舱状态条「近 N 日胜率」真实数据源。

因果：只读 recommendation_runs(已完成 formal) + recommendations(rec_) + recommendation_outcomes(已结算)，
按交易日取最近 N 个完成 formal 交易日聚合，未结算不计入分母（诚实不虚构胜率）。
"""
from app.services.outcome_service import recent_formal_win_rate

EXPECTED_KEYS = {
    "window_trade_days",
    "trade_dates",
    "sample_count",
    "success_count",
    "win_rate",
    "avg_return",
    "profit_loss_ratio",
}


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


def test_recent_formal_win_rate_window_bounds(db):
    """窗口边界不崩溃：单日与最大窗口均返回合法结构。"""
    one = recent_formal_win_rate(db, window_trade_days=1)
    assert EXPECTED_KEYS.issubset(one.keys())
    assert one["window_trade_days"] == 1

    wide = recent_formal_win_rate(db, window_trade_days=60)
    assert EXPECTED_KEYS.issubset(wide.keys())
    assert wide["window_trade_days"] == 60
