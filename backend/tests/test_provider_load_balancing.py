import random
from collections import Counter

from app.core import metrics
from app.services.datasource.capability import resolve_tiers
from app.services.tushare_transport import TushareProviderRouter


class _P:
    def __init__(self, name, rpm=100, headroom=1.0, health=1.0, priority=0):
        self.name = name
        self._h, self._hr = health, headroom
        self.config = type("C", (), {"rate_limit_per_minute": rpm, "priority": priority})()

    def supports(self, api):
        return True

    def lb_health(self, api):
        return self._h

    def lb_headroom(self, api):
        return self._hr


def _router(providers, tiers, **kw):
    return TushareProviderRouter(providers, api_routes={"daily": tuple(p.name for p in providers)},
                                 api_tiers={"daily": tiers}, **kw)


def test_distribution_follows_rate_headroom():
    a, b = _P("a", rpm=180), _P("b", rpm=120)
    r = _router([a, b], [("a", "b")], balance_rng=random.Random(7))
    firsts = Counter(r._ordered_providers("daily")[0].name for _ in range(3000))
    assert 0.55 < firsts["a"] / 3000 < 0.65  # 180/(180+120)=0.6
    # fallback chain preserved: every order contains both
    assert sorted(p.name for p in r._ordered_providers("daily")) == ["a", "b"]


def test_exhausted_headroom_shifts_load():
    a, b = _P("a", rpm=180, headroom=0.0), _P("b", rpm=120)
    r = _router([a, b], [("a", "b")], balance_deterministic=True)
    assert [p.name for p in r._ordered_providers("daily")] == ["b", "a"]


def test_breaker_open_weight_zero_but_still_fallback():
    a, b = _P("a", health=0.0, rpm=1000), _P("b")
    r = _router([a, b], [("a", "b")], balance_rng=random.Random(1))
    for _ in range(50):
        assert [p.name for p in r._ordered_providers("daily")] == ["b", "a"]


def test_tier_fallback_quality_first():
    hi, lo = _P("hi", rpm=10), _P("lo", rpm=1000)
    r = _router([hi, lo], [("hi",), ("lo",)], balance_rng=random.Random(3))
    for _ in range(50):
        assert [p.name for p in r._ordered_providers("daily")] == ["hi", "lo"]


def test_no_tiers_means_pure_chain_order():
    a, b = _P("a", headroom=0.0), _P("b")
    r = TushareProviderRouter([a, b], api_routes={"daily": ("a", "b")})
    assert [p.name for p in r._ordered_providers("daily")] == ["a", "b"]


def test_health_decay_on_real_provider(monkeypatch):
    from app.services import tushare_transport as tt

    clock = [1000.0]
    monkeypatch.setattr(tt.time, "monotonic", lambda: clock[0])
    p = tt.TushareHttpProvider(tt.ProviderConfig(name="x", base_url="http://x", credential="k",
                                                 auth_mode=tt.AuthMode.JSON_TOKEN, rate_limit_per_minute=60))
    p._lb_record("daily", True, weight=2.0)
    assert abs(p.lb_health("daily") - 1 / 3) < 1e-6
    clock[0] += p.LB_HALF_LIFE_S
    assert abs(p.lb_health("daily") - 1 / 2) < 1e-6
    clock[0] += 20 * p.LB_HALF_LIFE_S
    assert p.lb_health("daily") > 0.99


def test_weights_exposed_in_metrics():
    metrics.reset()
    r = _router([_P("a"), _P("b")], [("a", "b")], balance_rng=random.Random(0))
    r._ordered_providers("daily")
    snap = metrics.snapshot()
    assert {g["labels"]["provider"] for g in snap["gauges"] if g["name"] == "provider_lb_weight"} == {"a", "b"}
    assert any(c["name"] == "provider_lb_choice" for c in snap["counters"])


def test_balance_knobs():
    table = {"daily": {"tiers": (("a", "b"),), "balance": True},
             "top_list": {"tiers": (("a", "b"),), "balance": False}}
    assert set(resolve_tiers(table)) == {"daily"}
    assert resolve_tiers(table, enabled=False) == {}
