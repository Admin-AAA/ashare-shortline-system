#!/usr/bin/env python3
"""Probe configured Tushare-compatible sources without emitting credentials."""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings

_PROBE_INTERVAL_SECONDS = 1 / 30
_PROBE_CODE = "000001.SZ"
_PROBE_INDEX = "000001.SH"


def _compact_day(value: str) -> str:
    return value.replace("-", "")


def _probe_params(start_day: str, end_day: str) -> dict[str, dict[str, Any]]:
    return {
        "daily": {"ts_code": _PROBE_CODE, "start_date": start_day, "end_date": end_day},
        "daily_basic": {"ts_code": _PROBE_CODE, "start_date": start_day, "end_date": end_day},
        "adj_factor": {"ts_code": _PROBE_CODE, "start_date": start_day, "end_date": end_day},
        "stk_mins": {
            "ts_code": _PROBE_CODE,
            "freq": "1min",
            "start_date": f"{start_day} 09:00:00",
            "end_date": f"{end_day} 15:30:00",
        },
        "rt_k": {"ts_code": _PROBE_CODE},
        "stock_basic": {"exchange": "", "list_status": "L", "fields": "symbol,name,industry,list_date"},
        "trade_cal": {"exchange": "SSE", "start_date": start_day, "end_date": end_day, "is_open": "1"},
        "moneyflow": {"ts_code": _PROBE_CODE, "start_date": start_day, "end_date": end_day},
        "limit_list_d": {"trade_date": end_day},
        "top_list": {"trade_date": end_day},
        "income": {"ts_code": _PROBE_CODE, "period": end_day},
        "balancesheet": {"ts_code": _PROBE_CODE, "period": end_day},
        "cashflow": {"ts_code": _PROBE_CODE, "period": end_day},
        "anns_d": {"ts_code": _PROBE_CODE, "start_date": start_day, "end_date": end_day},
        "index_daily": {"ts_code": _PROBE_INDEX, "start_date": start_day, "end_date": end_day},
    }


def _classify_message(status_code: int | None, message: str) -> str:
    lowered = message.lower()
    if status_code in {401, 403} or any(
        term in lowered for term in ("权限", "未开通", "积分", "unauthorized", "permission")
    ):
        return "permission"
    if status_code == 429 or any(term in lowered for term in ("频率", "频次", "rate limit")):
        return "rate_limit"
    if status_code is not None and status_code >= 500:
        return "server_error"
    if any(term in lowered for term in ("参数", "parameter", "invalid")):
        return "invalid_request"
    return "provider_error"


def _extract_data(payload: Mapping[str, Any]) -> tuple[list[str], list[Any]]:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return [], []
    fields = [str(field) for field in data.get("fields") or []]
    items = list(data.get("items") or [])
    return fields, items


def _date_range(fields: list[str], items: list[Any]) -> dict[str, str | None]:
    date_values: list[str] = []
    for item in items:
        if isinstance(item, Mapping):
            values = item
        elif isinstance(item, (list, tuple)):
            values = dict(zip(fields, item))
        else:
            continue
        for key, value in values.items():
            key_text = str(key).lower()
            if "date" not in key_text and "period" not in key_text and "time" not in key_text:
                continue
            raw = str(value or "").strip()
            if raw and ("date" in key_text or "period" in key_text or len(raw) >= 8):
                date_values.append(raw[:19])
    return {
        "first": min(date_values) if date_values else None,
        "last": max(date_values) if date_values else None,
    }


def _probe_api(session: requests.Session, base_url: str, api_name: str, params: Mapping[str, Any]) -> dict[str, Any]:
    try:
        response = session.get(
            f"{base_url.rstrip('/')}/{api_name}",
            params=dict(params),
            timeout=15,
        )
    except requests.RequestException as exc:
        return {"status": "error", "category": "network", "error_type": type(exc).__name__}

    try:
        payload = response.json()
    except ValueError:
        return {
            "status": "error",
            "category": _classify_message(response.status_code, "non_json"),
            "http_status": response.status_code,
        }
    if not isinstance(payload, Mapping):
        return {"status": "error", "category": "malformed_response", "http_status": response.status_code}
    if response.status_code >= 400 or payload.get("code") not in (None, 0, "0"):
        return {
            "status": "error",
            "category": _classify_message(
                response.status_code,
                str(payload.get("msg") or payload.get("message") or ""),
            ),
            "http_status": response.status_code,
        }

    fields, items = _extract_data(payload)
    return {
        "status": "ok" if items else "empty",
        "http_status": response.status_code,
        "row_count": len(items),
        "fields": fields,
        "date_range": _date_range(fields, items),
    }


def probe_sources(*, start_date: str, end_date: str) -> dict[str, Any]:
    settings = get_settings()
    params_by_api = _probe_params(_compact_day(start_date), _compact_day(end_date))
    source_keys = {
        "ai_tool_key1": settings.tushare_ai_tool_key1.strip(),
        "ai_tool_key2": settings.tushare_ai_tool_key2.strip(),
    }
    report: dict[str, Any] = {
        "range_requested": {"start_date": start_date, "end_date": end_date},
        "sources": {},
    }
    last_request_at = 0.0
    for source_name, api_key in source_keys.items():
        source_report: dict[str, Any] = {"configured": bool(api_key), "apis": {}}
        if not api_key:
            source_report["status"] = "not_configured"
            report["sources"][source_name] = source_report
            continue
        session = requests.Session()
        session.trust_env = False
        proxy = settings.tushare_ai_tool_proxy.strip()
        if proxy:
            session.proxies = {"http": proxy, "https": proxy}
        session.headers.update({"X-API-Key": api_key, "Accept": "application/json"})
        try:
            for api_name, params in params_by_api.items():
                wait = _PROBE_INTERVAL_SECONDS - (time.monotonic() - last_request_at)
                if wait > 0:
                    time.sleep(wait)
                result = _probe_api(session, settings.tushare_ai_tool_url, api_name, params)
                last_request_at = time.monotonic()
                source_report["apis"][api_name] = result
        finally:
            session.close()
        statuses = [str(item.get("status")) for item in source_report["apis"].values()]
        source_report["status"] = "ok" if "ok" in statuses else "degraded" if "empty" in statuses else "error"
        report["sources"][source_name] = source_report
    return report


def main() -> None:
    today = date.today()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default=(today - timedelta(days=40)).isoformat())
    parser.add_argument("--end-date", default=today.isoformat())
    args = parser.parse_args()
    print(json.dumps(probe_sources(start_date=args.start_date, end_date=args.end_date), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
