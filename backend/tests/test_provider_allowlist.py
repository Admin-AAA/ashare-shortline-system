from app.services.tushare_transport import AuthMode, ProviderConfig, TushareHttpProvider, TushareProviderRouter


def _p(name, **kw):
    return TushareHttpProvider(ProviderConfig(name=name, base_url="http://x", credential="c",
                                              auth_mode=AuthMode.JSON_TOKEN, rate_limit_per_minute=0, **kw))


def test_allowlist_limits_provider_to_listed_apis():
    q = _p("quantdata", included_apis=frozenset({"trade_cal", "broker_recommend"}), priority=1)
    assert q.supports("trade_cal") and q.supports("broker_recommend")
    assert not q.supports("daily") and not q.supports("fina_indicator")


def test_allowlisted_provider_not_routed_for_other_apis():
    q = _p("quantdata", included_apis=frozenset({"trade_cal", "fina_indicator_vip", "broker_recommend"}), priority=1)
    pm = _p("promax", priority=10)
    r = TushareProviderRouter([q, pm])
    assert [p.name for p in r._ordered_providers("daily")] == ["promax"]
    assert [p.name for p in r._ordered_providers("fina_indicator")] == ["promax"]
    assert [p.name for p in r._ordered_providers("trade_cal")] == ["promax", "quantdata"]
    assert [p.name for p in r._ordered_providers("broker_recommend")][0] in {"quantdata", "promax"}
    assert "quantdata" in [p.name for p in r._ordered_providers("fina_indicator_vip")]


def test_real_quantdata_config_uses_allowlist():
    from app.services import tushare_client as tc
    assert tc.QUANTDATA_ALLOWED_APIS == {"fina_indicator_vip", "broker_recommend", "trade_cal"}
