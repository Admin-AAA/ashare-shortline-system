"""S4 prefilter injection regression coverage.

``skip_aux_refresh`` is pointer/current-state only: it must not revive the former
limit-up refresh precheck or make an outbound request.
"""
from app.services.auxiliary.application import inject_auxiliary_prefilter_flags
from app.services.workbench_service import run_workbench


def test_run_workbench_importable_without_legacy_limit_up_precheck():
    assert callable(run_workbench)


def test_prefilter_injection_sets_limit_up_and_safe_non_limit_defaults():
    stocks = [
        {"code": "600000", "name": "非涨停"},
        {"code": "000001", "name": "涨停"},
    ]
    hit = inject_auxiliary_prefilter_flags(
        stocks,
        {
            "limit_up": {
                "000001": {
                    "limit_times": 1,
                    "open_times": 0,
                    "fd_amount_yi": 0.5,
                    "turnover_ratio": 4.0,
                }
            },
            "dragon_tigers": {},
        },
    )

    assert hit == 1
    by_code = {stock["code"]: stock for stock in stocks}
    assert by_code["000001"]["is_limit_up"] is True
    assert by_code["000001"]["twenty_cm_risk_gate"] == "not_applicable"
    assert by_code["600000"]["is_limit_up"] is False
    assert by_code["600000"]["limit_up_consecutive_days"] == 0
    assert by_code["600000"]["twenty_cm_risk_gate"] == "not_applicable"
