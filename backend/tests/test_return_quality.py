"""收益质量 / 组合口径指标单测（app/services/return_quality.py）。

与 app/services/backtest/strategy_quality.py（选股质量闸门）是两件事，不要混。
"""
from __future__ import annotations

import pytest

from app.services.return_quality import portfolio_metrics, quality_metrics


def test_quality_empty_returns_none_not_zero():
    q = quality_metrics([])
    # 空样本必须是 None，不能是 0（否则「没数据」会被读成「零收益」）
    assert q["trim_avg_ret"] is None
    assert q["top10_profit_share_pct"] is None
    assert q["median_ret"] is None


def test_quality_small_sample_skips_trim():
    """低于 20 笔不做 trim，避免丢两笔就改变分布形状。"""
    q = quality_metrics([10.0, -4.0, 6.0])
    assert q["trim_avg_ret"] == pytest.approx(4.0)
    assert q["median_ret"] == 6.0
    assert q["worst_ret"] == -4.0


def test_quality_trim_reveals_tail_dependence():
    """20 笔里 2 笔大赚撑起全部利润：trim 后应转负。"""
    q = quality_metrics([50.0, 40.0] + [-3.0] * 18)
    assert q["trim_avg_ret"] is not None and q["trim_avg_ret"] < 0
    # 前 10 笔 = 50+40+(-3×8) = 66，净利润 = 90-54 = 36 → 183.3%
    assert q["top10_profit_share_pct"] == pytest.approx(183.3)


def test_quality_lottery_flag_over_100():
    """前 10 笔赚的比净利润还多（其余净亏）→ 占比 >100，即彩票型。"""
    q = quality_metrics([10.0] * 10 + [-2.0] * 10)
    # 盈利 100，亏损 20，净利润 80；前 10 笔 100 → 125%
    assert q["top10_profit_share_pct"] == pytest.approx(125.0)
    # 但集中度口径恒 ≤100%
    assert q["top10_share_of_gross_pct"] == pytest.approx(100.0)


def test_quality_lottery_undefined_when_net_negative():
    """净利润 ≤0 时占比无意义，返回 None 而不是编一个数。"""
    q = quality_metrics([10.0] + [-8.0] * 5)
    assert q["top10_profit_share_pct"] is None


def test_quality_median_above_mean_means_left_tail():
    """中位数高于均值 → 均值被左尾大亏拖累（陈小群型）。"""
    q = quality_metrics([0.5] * 8 + [-20.0])
    assert q["median_ret"] > q["trim_avg_ret"]


def test_portfolio_requires_dates():
    """没有可用日期就不产出字段，避免调用方把 {} 当零值。"""
    assert portfolio_metrics([{"ret": 5.0}, {"ret": -1.0}]) == {}


def test_portfolio_rejects_too_few_days():
    rows = [{"signal_date": "2026-01-0%d" % i, "ret": 1.0} for i in range(1, 5)]
    assert portfolio_metrics(rows) == {}


def test_portfolio_computes_drawdown_and_calmar():
    rows = [
        {"signal_date": "2026-01-01", "ret": 10.0},
        {"signal_date": "2026-01-02", "ret": -5.0},
        {"signal_date": "2026-01-03", "ret": -5.0},
        {"signal_date": "2026-01-04", "ret": 2.0},
        {"signal_date": "2026-01-05", "ret": 2.0},
        {"signal_date": "2026-01-06", "ret": 2.0},
    ]
    p = portfolio_metrics(rows, holding_days=5)
    assert p["portfolio_mdd_pct"] < 0
    assert p["portfolio_calmar"] is not None
    # 每笔持 5 日 → 平均持有天数 5
    assert p["avg_holding_days"] == pytest.approx(5.0)
    # 每天 1 笔、持 5 日 → 槽位 5 个，资金利用率 100%
    assert p["max_concurrent_batches"] == pytest.approx(5.0)
    assert p["capital_utilization_pct"] == pytest.approx(100.0)


def test_portfolio_accepts_replay_keys():
    """回放研究用 dt/net，画像用 signal_date/ret，两种都要认。"""
    rows = [{"dt": "2026-02-0%d" % i, "net": 1.5} for i in range(1, 7)]
    p = portfolio_metrics(rows, holding_days=5)
    assert p["portfolio_total_ret_pct"] > 0
    assert p["avg_holding_days"] == pytest.approx(5.0)


def test_portfolio_equity_weights_same_day_equally():
    """同日多笔按等权均值进入组合，不是简单相加。"""
    single = portfolio_metrics([{"signal_date": f"2026-03-{i:02d}", "ret": 2.0} for i in range(1, 6)])
    with_extra = [
        {"signal_date": f"2026-03-{i:02d}", "ret": 2.0} for i in range(1, 6)
    ] + [{"signal_date": "2026-03-01", "ret": -2.0}]
    p = portfolio_metrics(with_extra)
    # 3-01 当天两笔均值 0，其余四天各 +2% → 累计应低于单笔版
    assert p["portfolio_total_ret_pct"] < single["portfolio_total_ret_pct"]


def test_portfolio_rolling_book_does_not_inflate_returns():
    """槽位滚动组合：100 只在手（20 批 × 5 只）不应放大收益。

    老板 2026-11-15 纠正：资金等分 20 份、每天投 1 份、持 20 日后自动卖出，
    100 只在手只是 20 批，每只权重 1/100。组合日收益应等于每笔的日均收益
    （0.099%），而不是「每天把全部资金买当天新票」的 5 倍。
    """
    import datetime as dt

    d0 = dt.date(2026, 1, 1)
    rows = [
        {"signal_date": (d0 + dt.timedelta(days=i)).isoformat(), "ret": 2.0, "days": 20}
        for i in range(60)
        for _ in range(5)
    ]
    p = portfolio_metrics(rows)
    # 稳态 20 批 × 5 只 = 100 只，资金利用率 100%
    assert p["max_concurrent_batches"] == pytest.approx(20.0)
    assert p["capital_utilization_pct"] == pytest.approx(100.0)
    # 60 个交易日 × 日均 0.0990% → 累计约 6.1%，不是 60 × 2% = 120%
    # （100 只在手不放大收益：每只权重 1/100，组合日收益仍是单笔日均）
    assert p["portfolio_total_ret_pct"] == pytest.approx(6.1, abs=0.3)


def test_portfolio_reports_mixed_holding_periods():
    """持有期混合（5 日 + 30 日）时批 span 取最长，并报出持有期离散度。"""
    import datetime as dt

    d0 = dt.date(2026, 1, 1)
    rows = [
        {"signal_date": (d0 + dt.timedelta(days=i)).isoformat(), "ret": 1.0, "days": 5}
        for i in range(60)
    ] + [
        {"signal_date": (d0 + dt.timedelta(days=i)).isoformat(), "ret": 1.0, "days": 30}
        for i in range(60)
    ]
    p = portfolio_metrics(rows)
    # 同日混持有期 → span 取 30（最晚回款），槽位峰值 = 30
    assert p["max_concurrent_batches"] == pytest.approx(30.0)
    # 混合持有期必须报出离散度，让读者知道槽位语义已漂移
    assert p["holding_days_std"] is not None and p["holding_days_std"] > 0
    assert p["avg_holding_days"] == pytest.approx(17.5)


def test_portfolio_short_book_utilization_below_full():
    """短线每日 3 张、持 5 日 → 槽位 5 个、在手上限 15 只，不误报闲置。"""
    import datetime as dt

    d0 = dt.date(2026, 5, 1)
    rows = [
        {"signal_date": (d0 + dt.timedelta(days=i)).isoformat(), "ret": 1.2, "days": 5}
        for i in range(40)
        for _ in range(3)
    ]
    p = portfolio_metrics(rows)
    assert p["max_concurrent_batches"] == pytest.approx(5.0)
    assert p["capital_utilization_pct"] == pytest.approx(100.0)
    # 稳态在手上限 = 5 批 × 3 只
    assert p["avg_concurrent_positions"] < 15.0
