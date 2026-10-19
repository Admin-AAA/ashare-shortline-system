import inspect


def test_tushare_client_writes_none_for_missing_chip():
    import app.services.tushare_client as tc

    src = inspect.getsource(tc)
    assert '_f(chp.get("cost_50pct"))' not in src
    assert src.count('_f(chp.get("cost_50pct"), None)') == 2


def test_chip_health_domain_ignores_zero():
    from app.services.data_health_service import _DATA_DOMAIN_DEFS, _VALUE_SENTINEL_DEFS

    chip = next(d for d in _DATA_DOMAIN_DEFS if d["key"] == "chip")
    assert chip.get("positive_only") is True
    assert {"chip_cost_50", "ivol20"} <= {d["column"] for d in _VALUE_SENTINEL_DEFS}
