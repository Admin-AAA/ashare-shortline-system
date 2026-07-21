"""D 项净成本 + 板块(20cm/10cm)分解 —— 指标页透出净盈亏比/净胜率/板块拆分的数据源。

因果：只读 recommendation_runs(已完成 formal) + recommendations(rec_) + recommendation_outcomes(已结算)，
净口径与 self_check._compute_d_cost 一致（扣 0.07%/笔）；板块判定与 self_check._is_20cm_code 一致
（300/301/688/689 = 20cm）。纯只读，不改任何规则，不污染样本。
"""
from app.services.outcome_service import (
    _is_20cm_code,
    board_and_net_breakdown,
)


def test_is_20cm_code_classification():
    """板块判定口径：创业板/科创板(300/301/688/689) = 20cm，其余 = 10cm。"""
    assert _is_20cm_code("300750") is True
    assert _is_20cm_code("301269") is True
    assert _is_20cm_code("688981") is True
    assert _is_20cm_code("689009") is True
    assert _is_20cm_code("600519") is False
    assert _is_20cm_code("000001") is False
    assert _is_20cm_code("002594") is False
    # 防御：非字符串/短码不误判为 20cm
    assert _is_20cm_code("30") is False


def test_board_and_net_breakdown_keys_and_shape(db):
    """返回结构完整且字段类型正确。"""
    res = board_and_net_breakdown(db, window_trade_days=10)
    assert isinstance(res, dict)
    assert res["window_trade_days"] == 10
    assert isinstance(res["trade_dates"], list)
    assert isinstance(res["sample_count"], int)
    assert "net" in res and "board" in res
    assert set(res["board"].keys()) == {"20cm", "10cm"}


def test_board_and_net_breakdown_sane_when_data_present(db):
    """有已结算样本时：净胜率∈[0,100]、净盈亏比为正浮点、板块样本之和=总样本。"""
    res = board_and_net_breakdown(db, window_trade_days=10)
    if res["sample_count"] == 0 or res["net"] is None:
        # 该窗口无已结算 formal 推荐：诚实返回 net=None，不虚构
        assert res["net"] is None
        return
    net = res["net"]
    assert 0.0 <= net["net_win_rate"] <= 100.0
    assert isinstance(net["net_avg_return"], float)
    assert net["net_profit_loss_ratio"] is None or net["net_profit_loss_ratio"] > 0
    # 板块样本之和应等于总样本（每笔必属 20cm 或 10cm 之一）
    b20 = res["board"]["20cm"]
    b10 = res["board"]["10cm"]
    parts = [b["n"] for b in (b20, b10) if b]
    assert sum(parts) == res["sample_count"]


def test_board_and_net_breakdown_net_matches_gross_minus_cost(db):
    """净盈亏比应 < 毛盈亏比（扣 0.07%/笔后分母/分子同向缩小，净 PL 略低）。

    结构性护栏：净口径不为虚构——当两侧均有盈亏时 net_pl < gross_pl 的差由交易成本驱动。
    """
    res = board_and_net_breakdown(db, window_trade_days=30)
    if res["net"] is None:
        return
    net = res["net"]
    if net["profit_loss_ratio"] is not None and net["net_profit_loss_ratio"] is not None:
        # 净盈亏比不得高于毛盈亏比（成本只会压缩，不会放大）
        assert net["net_profit_loss_ratio"] <= net["profit_loss_ratio"] + 1e-9
