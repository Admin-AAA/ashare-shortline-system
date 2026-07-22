from scripts.backfill_moneyflow_concept import moneyflow_coverage_sufficient


def test_moneyflow_coverage_requires_ninety_percent_of_daily_universe() -> None:
    assert moneyflow_coverage_sufficient(300, 5_000) is False
    assert moneyflow_coverage_sufficient(4_499, 5_000) is False
    assert moneyflow_coverage_sufficient(4_500, 5_000) is True

