"""Decision inputs must be invariant to research window boundaries."""
import pytest

from app.services.backtest.research_runner import LocalResearchSnapshotBuilder
from app.services.factors import FactorContext
from app.services.factors.shortline import DragonTigerFactor


def test_prior_flow_handles_first_day_sparse_signals_and_future_rows():
    builder = object.__new__(LocalResearchSnapshotBuilder)
    flows = {day: {"600000": {"main_net_inflow_yi": 1}} for day in (
        "2026-09-04", "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10"
    )}
    assert builder._build_prior_date_map(["2026-09-07", "2026-09-09"], flows) == {
        "2026-09-07": "2026-09-04", "2026-09-09": "2026-09-08",
    }
    assert builder._build_prior_date_map(["2026-09-04"], flows) == {"2026-09-04": None}


@pytest.mark.parametrize("verified", [None, False, "false", "true", 1])
def test_institution_requires_explicit_verified_boolean(verified):
    result = DragonTigerFactor().compute({
        "dragon_tiger_is_on_list": True,
        "dragon_tiger_net_amount_yi": 1.2,
        "dragon_tiger_institution_net_buy_yi": 1.2,
        "dragon_tiger_institution_source_verified": verified,
    }, FactorContext())
    assert result["dragon_tiger_inst_buy_ratio"] == 0.0


def test_verified_institution_value_remains_usable():
    result = DragonTigerFactor().compute({
        "dragon_tiger_is_on_list": True,
        "dragon_tiger_net_amount_yi": 1.2,
        "dragon_tiger_institution_net_buy_yi": 0.6,
        "dragon_tiger_institution_source_verified": True,
    }, FactorContext())
    assert result["dragon_tiger_inst_buy_ratio"] == 0.5
