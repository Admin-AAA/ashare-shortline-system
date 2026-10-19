"""Data-driven provider routing: capability matrix -> per-API routing table.

The routing table (``routing_table.json`` next to this file) is the single source
of truth for which provider serves which API and in what order:

    {"apis": {"daily": {"order": ["promax", "quantdata"], "unsupported": ["newgw"]}}}

* ``order``       providers tried first, in order.
* ``unsupported`` providers never tried for that API (probe showed no permission/error).
* Providers in neither list (e.g. a newly added backup gateway) are appended after
  ``order`` by priority, so adding a provider = add credentials, re-probe, regenerate.
* APIs absent from the table fall back to priority order over every provider.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[3]
MATRIX_JSON = BACKEND_ROOT / "data" / "runtime" / "datasource_capability_matrix.json"
ROUTING_TABLE = Path(__file__).with_name("routing_table.json")

OK = ("ok", "empty")
TRANSIENT_ERRORS = ("http_rate_limit", "business_rate_limit", "quota_exhausted", "timeout", "network", "circuit_open", "http_server")
HARD_MARKERS = ("unknown_api", "not purchased", "permission", "no permission", "40203", "2003")


def classify(rec: Mapping[str, Any]) -> str:
    """ok | transient | unsupported | unverified.

    Only a hard "API not available for this credential" answer excludes a
    provider; rate limits and parameter-shape rejections keep it as a tail fallback.
    """
    if rec.get("status") in OK:
        return "ok"
    if rec.get("status") == "no_permission":
        return "unsupported"
    err = str(rec.get("error") or "")
    msg = str(rec.get("message") or "").lower()
    if err in TRANSIENT_ERRORS:
        return "transient"
    if any(m in msg for m in HARD_MARKERS):
        return "unsupported"
    return "unverified"


def load_matrix(path: Path = MATRIX_JSON) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def load_routing_table(path: Path = ROUTING_TABLE) -> dict[str, dict[str, tuple[str, ...]]]:
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for api, spec in (raw.get("apis") or {}).items():
        out[str(api)] = {
            "order": tuple(spec.get("order") or ()),
            "unsupported": tuple(spec.get("unsupported") or ()),
            "tiers": tuple(tuple(t) for t in spec.get("tiers") or ()),
            "balance": bool(spec.get("balance", True)),
        }
    return out


def _score(rec: Mapping[str, Any], last_trade_date: str) -> tuple:
    fresh = 1 if (rec.get("latest") or "")[:8] >= (last_trade_date or "")[:8] else 0
    return (
        -(1 if rec["status"] == "ok" else 0),
        -fresh,
        -float(rec.get("field_completeness") or 0),
        -int(rec.get("rows") or 0),  # coverage: more rows for the same window = more complete
        -int(rec.get("rate_limit_per_minute") or 0),  # headroom
        int(rec.get("latency_ms") or 0),
    )


def _tiers(good: list, ltd: str) -> list[list[str]]:
    """Group ranked providers into equal-quality tiers (balanced within, fallback across).

    Same tier = has data, same freshness, field completeness and row coverage both
    within 5% of the tier head.
    """
    tiers: list[list[str]] = []
    head = None
    for r in good:
        s = _score(r, ltd)
        if head is not None:
            hs = _score(head, ltd)
            same = (
                s[:2] == hs[:2]
                and -s[2] >= 0.95 * -hs[2]
                and -s[3] >= 0.95 * -hs[3]
            )
            if same:
                tiers[-1].append(r["provider"])
                continue
        head = r
        tiers.append([r["provider"]])
    return tiers


def derive_routes(matrix: Mapping[str, Any], *, spread: Mapping[str, int] | None = None) -> dict:
    """Rank providers per API from probe results.

    Ranking: has data > fresh to last trade date > field completeness > row coverage > rate
    headroom > latency.  ``spread`` caps how many APIs one provider may lead
    (load spreading): once a provider leads its cap, an equally-good runner-up
    (same status/freshness/completeness) is promoted instead.
    """
    ltd = matrix.get("last_trade_date", "")
    by_api: dict[str, list] = {}
    for r in matrix.get("rows", []):
        by_api.setdefault(r["api"], []).append(r)
    leads: dict[str, int] = {}
    apis: dict[str, dict] = {}
    for api in sorted(by_api):
        recs = by_api[api]
        good = sorted((r for r in recs if r["status"] in OK), key=lambda r: _score(r, ltd))
        bad = sorted(r["provider"] for r in recs if classify(r) == "unsupported")
        tail = sorted(r["provider"] for r in recs if classify(r) in ("transient", "unverified"))
        if spread and len(good) > 1:
            head = good[0]
            cap = spread.get(head["provider"])
            if cap is not None and leads.get(head["provider"], 0) >= cap:
                for i, alt in enumerate(good[1:], 1):
                    if _score(alt, ltd)[:4] == _score(head, ltd)[:4]:
                        good.insert(0, good.pop(i))
                        break
        order = [r["provider"] for r in good] + tail
        if order:
            leads[order[0]] = leads.get(order[0], 0) + 1
        apis[api] = {"order": order, "unsupported": bad, "tiers": _tiers(good, ltd)}
    return {"generated_from": matrix.get("probed_at"), "apis": apis}


def diff_matrices(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[str]:
    def idx(m):
        return {(r["provider"], r["api"]): r["status"] for r in m.get("rows", [])}

    o, n = idx(old), idx(new)
    out = []
    for key in sorted(set(o) & set(n)):
        a, b = o[key] in OK, n[key] in OK
        if a != b:
            out.append(f"{key[0]}/{key[1]}: {o[key]} -> {n[key]}")
    return out


def render_markdown(matrix: Mapping[str, Any], drift: list[str] | None = None) -> str:
    rows = matrix.get("rows", [])
    providers = sorted({r["provider"] for r in rows})
    apis = sorted({r["api"] for r in rows})
    cell = {(r["provider"], r["api"]): r for r in rows}
    sym = {"ok": "✅", "empty": "⚪", "no_permission": "⛔", "error": "❌"}
    lines = [
        "# 数据源能力矩阵",
        "",
        f"实测时间：{matrix.get('probed_at')}，最近交易日 {matrix.get('last_trade_date')}。"
        " 由 `scripts/probe_datasource_capabilities.py` 生成（每 provider×api 1 次小调用），勿手改。",
        "",
        "图例：✅有数据 ⚪调用成功但空 ⛔无权限 ❌错误；格内为 行数/最新日期/字段完整度/延迟。",
        "",
        "| api | " + " | ".join(providers) + " |",
        "|---|" + "---|" * len(providers),
    ]
    for api in apis:
        cells = []
        for p in providers:
            r = cell.get((p, api))
            if not r:
                cells.append("")
                continue
            if r["status"] in OK:
                cells.append(f"{sym[r['status']]} {r['rows']}/{r.get('latest') or '-'}/{r.get('field_completeness', 0):.0%}/{r['latency_ms']}ms")
            else:
                cells.append(f"{sym.get(r['status'], '?')} {classify(r)}:{r.get('error', '')}")
        lines.append(f"| {api} | " + " | ".join(cells) + " |")
    lines += ["", "## 限速（配置值，次/分钟）", ""]
    for p in providers:
        rl = next((r["rate_limit_per_minute"] for r in rows if r["provider"] == p), None)
        lines.append(f"- {p}: {rl}")
    if drift:
        lines += ["", "## 与上次实测相比的漂移", ""] + [f"- {d}" for d in drift]
    return "\n".join(lines) + "\n"


def _default_spread(matrix: Mapping[str, Any]) -> dict[str, int]:
    """Lead caps proportional to each provider's configured rate limit."""
    rl: dict[str, int] = {}
    for r in matrix.get("rows", []):
        rl[r["provider"]] = int(r.get("rate_limit_per_minute") or 0)
    total = sum(rl.values()) or 1
    n = len({r["api"] for r in matrix.get("rows", [])})
    return {p: max(1, -(-n * v // total)) for p, v in rl.items()}


def build_routing_table(matrix: Mapping[str, Any], previous: Mapping[str, Any] | None = None) -> dict:
    """Derive the routing table and re-apply the hand-maintained ``manual`` section.

    ``manual``: {api: {"unsupported": [...], "first": [...], "note": "..."}} for
    facts a 1-call probe can't see (e.g. a gateway rejecting every param shape the
    project uses).  Manual entries survive regeneration.
    """
    derived = derive_routes(matrix, spread=_default_spread(matrix))
    manual = dict((previous or {}).get("manual") or {})
    for api, spec in manual.items():
        entry = derived["apis"].setdefault(api, {"order": [], "unsupported": []})
        unsupported = set(entry["unsupported"]) | set(spec.get("unsupported") or ())
        first = [p for p in spec.get("first") or () if p not in unsupported]
        order = first + [p for p in entry["order"] if p not in unsupported and p not in first]
        entry["order"], entry["unsupported"] = order, sorted(unsupported)
        tiers = [[p for p in t if p not in unsupported] for t in entry.get("tiers") or []]
        if first:  # pinned providers form their own leading tier
            tiers = [list(first)] + [[p for p in t if p not in first] for t in tiers]
        entry["tiers"] = [t for t in tiers if t]
        if "balance" in spec:
            entry["balance"] = bool(spec["balance"])
    derived["manual"] = manual
    derived["_doc"] = (
        "Generated by scripts/probe_datasource_capabilities.py --write-routes from the "
        "capability matrix; edit only the 'manual' section by hand."
    )
    return derived


def resolve_routes(
    table: Mapping[str, Mapping[str, tuple[str, ...]]],
    defaults: Mapping[str, tuple[str, ...]],
) -> tuple[dict[str, tuple[str, ...]], dict[str, frozenset[str]]]:
    """Merge the table onto code defaults.

    Returns (api_routes, excluded_by_provider).  Table order wins; providers the
    table does not mention keep their relative default order behind it.
    """
    routes = {k: tuple(v) for k, v in defaults.items()}
    excluded: dict[str, set[str]] = {}
    for api, spec in table.items():
        order = tuple(spec.get("order") or ())
        bad = set(spec.get("unsupported") or ())
        tail = tuple(p for p in routes.get(api, ()) if p not in order and p not in bad)
        routes[api] = order + tail
        for p in bad:
            excluded.setdefault(p, set()).add(api)
    return routes, {p: frozenset(a) for p, a in excluded.items()}


def resolve_tiers(table: Mapping[str, Mapping[str, Any]], *, enabled: bool = True) -> dict[str, tuple[tuple[str, ...], ...]]:
    """api -> equal-quality tiers for load balancing; omitted when disabled globally or per api."""
    if not enabled:
        return {}
    return {
        api: tuple(tuple(t) for t in spec.get("tiers") or ())
        for api, spec in table.items()
        if spec.get("balance", True) and any(len(t) > 1 for t in spec.get("tiers") or ())
    }
