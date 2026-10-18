import json

from app.services.datasource.capability import (
    build_routing_table,
    classify,
    derive_routes,
    diff_matrices,
    load_routing_table,
    resolve_routes,
)


def _row(p, api, status="ok", rows=10, latest="20261016", fc=1.0, rl=100, ms=100, **kw):
    return dict(provider=p, api=api, status=status, rows=rows, latest=latest,
                field_completeness=fc, rate_limit_per_minute=rl, latency_ms=ms, **kw)


def _m(*rows):
    return {"probed_at": "t", "last_trade_date": "20261016", "rows": list(rows)}


def test_quality_and_freshness_rank_before_rate_headroom():
    m = _m(_row("a", "daily", rl=500, latest="20261015"), _row("b", "daily", rl=10),
           _row("c", "fina", fc=0.08, rl=500), _row("d", "fina", fc=1.0, rl=10))
    t = derive_routes(m)["apis"]
    assert t["daily"]["order"] == ["b", "a"]
    assert t["fina"]["order"] == ["d", "c"]


def test_hard_unsupported_excluded_but_transient_kept_as_tail():
    m = _m(_row("a", "x", status="error", error="provider_error", message="2003: api not purchased"),
           _row("b", "x", status="error", error="http_rate_limit"),
           _row("c", "x"))
    t = derive_routes(m)["apis"]["x"]
    assert t["order"] == ["c", "b"] and t["unsupported"] == ["a"]
    assert classify(m["rows"][1]) == "transient"


def test_fallback_chain_and_new_provider_is_appended():
    table = {"daily": {"order": ("p1", "p2"), "unsupported": ("bad",)}}
    routes, excluded = resolve_routes(table, {"daily": ("p2", "newcomer", "bad")})
    assert routes["daily"] == ("p1", "p2", "newcomer")
    assert excluded == {"bad": frozenset({"daily"})}


def test_manual_section_survives_regeneration():
    prev = {"manual": {"anns_d": {"unsupported": ["a"]}, "stk_mins": {"first": ["b"]}}}
    m = _m(_row("a", "anns_d"), _row("a", "stk_mins", rl=999), _row("b", "stk_mins", status="empty"))
    t = build_routing_table(m, prev)
    assert t["apis"]["anns_d"]["unsupported"] == ["a"] and t["apis"]["anns_d"]["order"] == []
    assert t["apis"]["stk_mins"]["order"][0] == "b"
    assert t["manual"] == prev["manual"]


def test_load_spreading_promotes_equal_runner_up():
    rows = []
    for api in ("a1", "a2", "a3", "a4"):
        rows += [_row("big", api, rl=200), _row("small", api, rl=100)]
    t = derive_routes(_m(*rows), spread={"big": 2, "small": 2})["apis"]
    assert sum(1 for s in t.values() if s["order"][0] == "small") == 2


def test_drift_detection():
    assert diff_matrices(_m(_row("a", "x")), _m(_row("a", "x", status="no_permission"))) == ["a/x: ok -> no_permission"]


def test_checked_in_table_is_loadable_and_has_fallbacks():
    t = load_routing_table()
    assert t["daily"]["order"][:1] and len(t["daily"]["order"]) >= 2
    assert "promax" in t["fina_indicator_vip"]["unsupported"]


def test_new_provider_with_unprobed_api_uses_priority(tmp_path):
    p = tmp_path / "rt.json"
    p.write_text(json.dumps({"apis": {}}))
    assert load_routing_table(p) == {}
