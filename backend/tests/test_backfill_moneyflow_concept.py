from scripts.backfill_moneyflow_concept import moneyflow_coverage_sufficient, moneyflow_net_yi, MONEYFLOW_WAN_TO_YI


def test_moneyflow_coverage_requires_ninety_percent_of_daily_universe() -> None:
    assert moneyflow_coverage_sufficient(300, 5_000) is False
    assert moneyflow_coverage_sufficient(4_499, 5_000) is False
    assert moneyflow_coverage_sufficient(4_500, 5_000) is True


def test_moneyflow_net_yi_converts_wan_to_yi() -> None:
    """Tushare moneyflow 金额字段单位是万元，转亿用 /1e4（非 /1e8）。

    回归 bug：旧代码 /1e8 致 main_net_inflow_yi 全是 ~0 小值（万元/亿=1e-4 量级），
    ratio 因分母接近 0 产出 -3909310 异常值。5000 万元净流入 = 0.05 亿。
    """
    assert moneyflow_net_yi(5000, 0) == 0.5  # 5000 万买入 = 0.5 亿
    assert moneyflow_net_yi(0, 3000) == -0.3  # 3000 万卖出 = -0.3 亿
    assert moneyflow_net_yi(10000, 4000) == 0.6  # 净 6000 万 = 0.6 亿
    assert MONEYFLOW_WAN_TO_YI == 1e4  # 锁定单位常量，防回退到 /1e8


def test_moneyflow_net_yi_handles_none_and_zero() -> None:
    """None/空值容错为 0，不抛异常。"""
    assert moneyflow_net_yi(None, None) == 0.0
    assert moneyflow_net_yi(0, 0) == 0.0

