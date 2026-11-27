"""收益质量指标单测（app/services/return_quality.py）。

与 app/services/backtest/strategy_quality.py（选股质量闸门）是两件事，不要混。
账户组合口径的测试在 test_overlap_portfolio.py（旧 portfolio_metrics 已于 2026-11-27 删除，L-203 修订）。
"""
from __future__ import annotations

import pytest

from app.services.return_quality import quality_metrics


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
