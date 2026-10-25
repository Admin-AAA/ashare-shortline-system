"""Probe every configured Tushare-compatible provider x every API the system uses.

Cheap and rate-limit respecting: one small call per (provider, api), going through
each provider's own limiter.  Writes data/runtime/datasource_capability_matrix.json
and docs/datasource_capability_matrix.md, and reports drift vs the previous run.

Usage: PYTHONPATH=. .venv/bin/python scripts/probe_datasource_capabilities.py [--apis a,b] [--providers p,q]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.datasource.capability import (  # noqa: E402
    MATRIX_JSON,
    ROUTING_TABLE,
    build_routing_table,
    diff_matrices,
    load_matrix,
    render_markdown,
    routing_table_drift,
)

D = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
D30 = (date.today() - timedelta(days=30)).strftime("%Y%m%d")
D10 = (date.today() - timedelta(days=10)).strftime("%Y%m%d")
S = "000001.SZ"

# api -> (params, fields); recent window so freshness is observable.
PROBES: dict[str, dict] = {
    "daily": {"ts_code": S, "start_date": D30, "end_date": D},
    "adj_factor": {"ts_code": S, "start_date": D30, "end_date": D},
    "daily_basic": {"ts_code": S, "start_date": D30, "end_date": D},
    "stk_limit": {"ts_code": S, "start_date": D30, "end_date": D},
    "trade_cal": {"exchange": "SSE", "start_date": D30, "end_date": D},
    "stock_basic": {"ts_code": S},
    "namechange": {"ts_code": S},
    "suspend_d": {"start_date": D10, "end_date": D},
    "moneyflow": {"ts_code": S, "start_date": D30, "end_date": D},
    "moneyflow_hsgt": {"start_date": D30, "end_date": D},
    "hsgt_top10": {"start_date": D10, "end_date": D, "market_type": "1"},
    "hk_hold": {"ts_code": S, "start_date": D30, "end_date": D},
    "top_list": {"trade_date": None},
    "top_inst": {"trade_date": None},
    "limit_list_d": {"trade_date": None},
    "block_trade": {"start_date": D10, "end_date": D},
    "margin": {"start_date": D10, "end_date": D},
    "margin_detail": {"ts_code": S, "start_date": D30, "end_date": D},
    "ths_index": {"exchange": "A", "type": "N"},
    "ths_member": {"ts_code": "885311.TI"},
    "stk_mins": {"ts_code": S, "freq": "5min", "start_date": f"{D} 09:30:00", "end_date": f"{D} 10:30:00"},
    "rt_k": {"ts_code": S},
    "rt_min": {"ts_code": S, "freq": "1MIN"},
    "stk_auction": {"ts_code": S, "start_date": D10, "end_date": D},
    "stk_factor": {"ts_code": S, "start_date": D30, "end_date": D},
    "stk_factor_pro": {"ts_code": S, "start_date": D30, "end_date": D},
    "cyq_perf": {"ts_code": S, "start_date": D30, "end_date": D},
    "index_daily": {"ts_code": "000300.SH", "start_date": D30, "end_date": D},
    "fina_indicator": {"ts_code": S},
    "fina_indicator_vip": {"period": "20250630"},
    "broker_recommend": {"month": (date.today().replace(day=1)).strftime("%Y%m")},
    "anns_d": {"ann_date": D},
    "share_float": {"ts_code": S},
    "stk_holdernumber": {"ts_code": S},
    "stk_holdertrade": {"start_date": D30, "end_date": D},
    "pledge_stat": {"ts_code": S},
    "repurchase": {"start_date": D30, "end_date": D},
    "stk_surv": {"start_date": D30, "end_date": D},
}
DATE_COLS = ("trade_date", "cal_date", "ann_date", "end_date", "month", "suspend_date", "float_date")


def _last_trade_date(router) -> str:
    try:
        env = router.request("trade_cal", params={"exchange": "SSE", "start_date": D30, "end_date": D, "is_open": "1"}, fields="cal_date")
        items = env["data"]["items"]
        return max(str(r[0]) for r in items)
    except Exception:
        return D


def probe(apis: list[str], providers_filter: set[str] | None) -> dict:
    from app.services.tushare_client import _build_provider_router

    router, _ = _build_provider_router()
    providers = [p for p in router._providers.values() if not providers_filter or p.name in providers_filter]
    ltd = _last_trade_date(router)
    rows = []
    for api in apis:
        params = {k: (ltd if v is None else v) for k, v in PROBES[api].items()}
        for p in providers:
            rec = {"provider": p.name, "api": api, "rate_limit_per_minute": p.config.rate_limit_per_minute}
            t0 = time.perf_counter()
            try:
                env = p.request(api, params=params, fields="")
                data = env.get("data") or {}
                fields = list(data.get("fields") or [])
                items = list(data.get("items") or [])
                latest = None
                for col in DATE_COLS:
                    if col in fields and items:
                        i = fields.index(col)
                        vals = [str(r[i]) for r in items if r[i]]
                        latest = max(vals) if vals else None
                        break
                rec.update(status="ok" if items else "empty", rows=len(items), fields=fields, latest=latest)
            except Exception as exc:  # classify, never abort the sweep
                cat = str(getattr(exc, "category", "") or type(exc).__name__)
                rec.update(status="no_permission" if cat == "permission" else "error", error=cat,
                           message=str(exc)[:160], rows=0, fields=[], latest=None)
            rec["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            rows.append(rec)
            print(f"{api:20s} {p.name:10s} {rec['status']:13s} rows={rec['rows']:<5} latest={rec['latest']} {rec['latency_ms']}ms {rec.get('error','')}", flush=True)
    # field completeness vs the widest successful field set for that api
    for api in apis:
        best = max((len(r["fields"]) for r in rows if r["api"] == api), default=0)
        for r in rows:
            if r["api"] == api:
                r["field_completeness"] = round(len(r["fields"]) / best, 3) if best and r["fields"] else 0.0
                r.pop("fields")
    return {"probed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "last_trade_date": ltd, "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apis", default="")
    ap.add_argument("--providers", default="")
    ap.add_argument("--write-routes", action="store_true", help="regenerate app/services/datasource/routing_table.json")
    ap.add_argument("--from-existing", action="store_true", help="skip probing; re-render from the saved matrix")
    ap.add_argument("--dry-run", action="store_true", help="do not overwrite matrix files")
    ap.add_argument("--fix-routes", action="store_true",
                    help="路由表与矩阵漂移（provider 已无权限仍在 order 中等）时自动重写 routing_table.json（默认只告警）")
    a = ap.parse_args()
    apis = [x for x in a.apis.split(",") if x] or list(PROBES)
    old = load_matrix()
    new = old if a.from_existing and old else probe(apis, set(x for x in a.providers.split(",") if x) or None)
    drift = diff_matrices(old, new) if old else []
    for d in drift:
        print("DRIFT", d)
    if not a.dry_run:
        MATRIX_JSON.parent.mkdir(parents=True, exist_ok=True)
        MATRIX_JSON.write_text(json.dumps(new, ensure_ascii=False, indent=1))
        (ROOT / "docs" / "datasource_capability_matrix.md").write_text(render_markdown(new, drift))
    if a.write_routes and not a.dry_run:
        prev = json.loads(ROUTING_TABLE.read_text()) if ROUTING_TABLE.exists() else None
        ROUTING_TABLE.write_text(json.dumps(build_routing_table(new, prev), ensure_ascii=False, indent=1) + "\n")
        print("routing table written:", ROUTING_TABLE)
    # 路由表漂移：矩阵显示某 provider 已无权限，路由表仍把它排前面 → 每个请求先白白失败一次。
    # 之前只有矩阵自身的 DRIFT 打印，没人对照路由表（hsgt_top10/moneyflow_hsgt 的 promax 无权限却仍排第一）。
    routing_drift: list[str] = []
    if ROUTING_TABLE.exists():
        routing_drift = routing_table_drift(new, json.loads(ROUTING_TABLE.read_text()))
        for d in routing_drift:
            print("ROUTING_DRIFT", d)
        if routing_drift and a.fix_routes and not a.dry_run:
            prev = json.loads(ROUTING_TABLE.read_text())
            ROUTING_TABLE.write_text(json.dumps(build_routing_table(new, prev), ensure_ascii=False, indent=1) + "\n")
            print("routing table auto-fixed:", ROUTING_TABLE)
            routing_drift = routing_table_drift(new, json.loads(ROUTING_TABLE.read_text()))
        try:
            from app.core.alerts import active_alert_records, emit_alert, resolve_alert

            for d in routing_drift:
                emit_alert("routing_drift", d.split(":", 1)[0], d)
            still = {d.split(":", 1)[0] for d in routing_drift}
            for rec in active_alert_records("routing_drift"):
                if str(rec["key"]) not in still:
                    resolve_alert("routing_drift", str(rec["key"]), "路由表已与能力矩阵一致")
        except Exception as exc:  # noqa: BLE001 - 告警失败不影响探测结果
            print("routing drift alert failed:", exc)
    # 退出码：矩阵漂移本身只是信息（已写日志与 docs），不该让 launchd 一直显示失败；
    # 仍存在未处理的路由漂移才返回 1。
    return 1 if routing_drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
