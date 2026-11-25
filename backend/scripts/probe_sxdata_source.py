"""sxdata（tus.sxdata.top）Tushare 兼容源评估探测。

目的：判断是否接入本项目数据网关。覆盖三块：
  1) sweep   系统在用的 57 个 Tushare 接口逐个实测（权限 / 行数 / 字段 / 最新日期 / 延迟）
  2) range   关键数据集的历史覆盖边界（日线 / 涨跌停 / 两融 / 分钟线 / 财务等）
  3) perf    单次延迟、并发 QPS、持续 60s 的每分钟吞吐与限流表现；备用 token 对比
  4) mcp     MCP 端点（/tu_share/mcp）可用性
  5) compare 与现有 provider（teajoin/quantdata）同参数对比延迟

token 只从环境变量读取，不写入任何输出文件：
  SXDATA_TOKEN=... SXDATA_BACKUP_TOKEN=... .venv/bin/python scripts/probe_sxdata_source.py

用法：
  .venv/bin/python scripts/probe_sxdata_source.py --phase all
  .venv/bin/python scripts/probe_sxdata_source.py --phase perf --out /tmp/sxdata_perf.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]  # backend/scripts -> repo root
DEFAULT_OUT = ROOT / "docs" / "sxdata_probe_results.json"
API_URL = os.environ.get("SXDATA_URL", "http://tus.sxdata.top/tu_share/")
MCP_URL = os.environ.get("SXDATA_MCP_URL", "http://tus.sxdata.top/tu_share/mcp")

TOKENS = {
    "primary": os.environ.get("SXDATA_TOKEN", "").strip(),
    "backup": os.environ.get("SXDATA_BACKUP_TOKEN", "").strip(),
}

_TLS = threading.local()


def _session() -> requests.Session:
    s = getattr(_TLS, "s", None)
    if s is None:
        s = requests.Session()
        _TLS.s = s
    return s


def _classify_error(msg: str) -> str:
    low = msg.lower()
    if any(k in msg for k in ("权限", "积分", "没有接口", "无权", "40203")) or "permission" in low:
        return "no_permission"
    if any(k in msg for k in ("每分钟", "频率", "限流", "频繁", "超过访问", "最大访问")) or "too many" in low or "rate limit" in low:
        return "rate_limited"
    if any(k in msg for k in ("参数", "不存在", "invalid")):
        return "invalid_params"
    return "error"


DATE_COLS = ("trade_date", "cal_date", "ann_date", "end_date", "float_date", "suspend_date",
             "pub_date", "report_date", "month")


def _date_bounds(fields: list[str], items: list[list]) -> tuple[str | None, str | None]:
    for col in DATE_COLS:
        if col in fields and items:
            i = fields.index(col)
            vals = [str(r[i]) for r in items if r[i] not in (None, "")]
            if vals:
                return min(vals), max(vals)
    return None, None


def call(
    api: str,
    params: dict,
    token_key: str = "primary",
    timeout: tuple[float, float] = (10.0, 90.0),
    fields: str = "",
    retries: int = 0,
) -> dict:
    token = TOKENS.get(token_key, "")
    rec: dict = {"api": api, "token": token_key, "params": params}
    if not token:
        rec.update(status="no_token", ms=0, rows=0, error="token missing")
        return rec
    t0 = time.perf_counter()
    attempts = retries + 1
    for attempt in range(attempts):
        try:
            r = _session().post(
                API_URL,
                json={"api_name": api, "token": token, "params": params, "fields": fields},
                timeout=timeout,
            )
            ms = (time.perf_counter() - t0) * 1000
            rec.update(http_status=r.status_code, ms=round(ms, 1), bytes=len(r.content or b""),
                       attempts=attempt + 1)
            if r.status_code != 200:
                status = "gateway_timeout" if r.status_code == 504 else "http_error"
                rec.update(status=status, rows=0, error=f"HTTP {r.status_code}: {r.text[:120]}")
                if r.status_code >= 500 and attempt + 1 < attempts:
                    time.sleep(8)
                    continue
                return rec
            env = r.json()
            code = env.get("code")
            msg = str(env.get("msg") or "")
            if code not in (0, None):
                rec.update(status=_classify_error(msg), rows=0, error=f"code={code} msg={msg[:200]}")
                # 503 upstream_pool_exhausted / 5xx 视为容量忙，重试一次；权限/参数类不重试
                if "upstream_pool_exhausted" in msg and attempt + 1 < attempts:
                    time.sleep(8)
                    continue
                return rec
            data = env.get("data") or {}
            fs = list(data.get("fields") or [])
            items = list(data.get("items") or [])
            lo, hi = _date_bounds(fs, items)
            rec.pop("error", None)
            rec.update(status="ok" if items else "empty", rows=len(items), fields=fs,
                       first_date=lo, latest=hi, msg=msg[:100] if msg and msg != "ok" else None)
            if items and api in ("fina_indicator", "stk_holdernumber"):
                rec["sample"] = [dict(zip(fs, r)) for r in items[:2]]
            return rec
        except requests.Timeout:
            rec.update(status="timeout", ms=round((time.perf_counter() - t0) * 1000, 1), rows=0,
                       error="client timeout", attempts=attempt + 1)
            if attempt + 1 < attempts:
                time.sleep(5)
                continue
            return rec
        except Exception as exc:  # noqa: BLE001 - 探测不中断
            rec.update(status="client_error", ms=round((time.perf_counter() - t0) * 1000, 1), rows=0,
                       error=f"{type(exc).__name__}: {str(exc)[:160]}", attempts=attempt + 1)
            if attempt + 1 < attempts:
                time.sleep(5)
                continue
            return rec
    return rec


def _last_trade_date() -> str:
    """最近一个已到期的交易日（trade_cal 含全年未来日期，必须按今天过滤）。"""
    today_s = date.today().strftime("%Y%m%d")
    rec = call("trade_cal", {"exchange": "SSE", "start_date": "20260101", "end_date": today_s, "is_open": "1"})
    if rec.get("status") == "ok" and rec.get("latest"):
        vals = [str(v) for v in (rec.get("latest"),) if v and str(v) <= today_s]
        if vals:
            return max(vals)
    today = date.today()
    while today.weekday() >= 5:
        today -= timedelta(days=1)
    return today.strftime("%Y%m%d")


S = "000001.SZ"


def _probe_defs(ltd: str) -> dict[str, dict]:
    today = date.today()
    D = (today - timedelta(days=1)).strftime("%Y%m%d")
    D10 = (today - timedelta(days=10)).strftime("%Y%m%d")
    D30 = (today - timedelta(days=30)).strftime("%Y%m%d")
    return {
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
        "margin": {"trade_date": None},
        "margin_detail": {"ts_code": S, "start_date": D30, "end_date": D},
        "ths_index": {"exchange": "A", "type": "N"},
        "ths_member": {"ts_code": "885311.TI"},
        "stk_mins": {"ts_code": S, "freq": "5min", "start_date": f"{ltd} 09:30:00", "end_date": f"{ltd} 15:00:00"},
        "rt_k": {"ts_code": S},
        "rt_min": {"ts_code": S, "freq": "1MIN"},
        "stk_auction": {"ts_code": S, "start_date": D10, "end_date": D},
        "stk_factor": {"ts_code": S, "start_date": D30, "end_date": D},
        "stk_factor_pro": {"ts_code": S, "start_date": D30, "end_date": D},
        "cyq_perf": {"ts_code": S, "start_date": D30, "end_date": D},
        "index_daily": {"ts_code": "000300.SH", "start_date": D30, "end_date": D},
        "fina_indicator": {"ts_code": S},
        "fina_indicator_vip": {"period": "20250630"},
        "broker_recommend": {"month": (today.replace(day=1)).strftime("%Y%m")},
        "anns_d": {"ann_date": ltd},
        "share_float": {"ts_code": S},
        "stk_holdernumber": {"ts_code": S},
        "stk_holdertrade": {"start_date": D30, "end_date": D},
        "pledge_stat": {"ts_code": S},
        "repurchase": {"start_date": D30, "end_date": D},
        "stk_surv": {"start_date": D30, "end_date": D},
        # 文本层（teajoin 扩展接口，标准 tushare 无）
        "report_rc": {"ts_code": S},
        "forecast": {"start_date": D10, "end_date": D},
        "forecast_vip": {"ts_code": S},
        "ths_hot": {"trade_date": None},
        "kpl_list": {"start_date": D10, "end_date": D},
        "limit_list_ths": {"trade_date": None},
        "st": {"start_date": D30, "end_date": D},
        "idx_anns": {"start_date": D10, "end_date": D},
        "express": {"start_date": D10, "end_date": D},
        "express_vip": {"ts_code": S},
        "fina_audit": {"start_date": D30, "end_date": D},
        "eco_cal": {"start_date": D10, "end_date": D},
        "cn_schedule": {},
        "stk_alert": {"start_date": D10, "end_date": D},
        "stk_managers": {"ts_code": S},
        "dividend": {"ts_code": S},
        "stk_shock": {"start_date": D30, "end_date": D},
        "stk_high_shock": {"start_date": D30, "end_date": D},
        "kpl_concept_cons": {"trade_date": None},
    }


def phase_sweep(ltd: str, apis_filter: list[str] | None = None) -> list[dict]:
    defs = _probe_defs(ltd)
    rows = []
    for api, params in defs.items():
        if apis_filter and api not in apis_filter:
            continue
        p = {k: (ltd if v is None else v) for k, v in params.items()}
        rec = call(api, p, "primary", retries=1)
        rec["params"] = p
        rows.append(rec)
        print(f"{api:20s} {rec['status']:14s} rows={rec.get('rows', 0):<5} "
              f"latest={rec.get('latest') or '-':10s} {rec.get('ms', 0):>7.1f}ms "
              f"{(rec.get('error') or '')[:70]}", flush=True)
    return rows


def phase_range(ltd: str) -> list[dict]:
    D30 = (date.today() - timedelta(days=30)).strftime("%Y%m%d")
    cases: list[tuple[str, str, dict]] = [
        # 日线：个股最早 + 全市场按日（含截断检查）
        ("daily", "000001.SZ 1990-1992", {"ts_code": S, "start_date": "19901219", "end_date": "19921231"}),
        ("daily", "000001.SZ 1993", {"ts_code": S, "start_date": "19930101", "end_date": "19931231"}),
        ("daily", "000001.SZ 2005-01", {"ts_code": S, "start_date": "20050101", "end_date": "20050131"}),
        ("daily", "全市场 20050104", {"trade_date": "20050104"}),
        ("daily", "全市场 20100104", {"trade_date": "20100104"}),
        ("daily", "全市场 20150105", {"trade_date": "20150105"}),
        ("daily", "全市场 20180102", {"trade_date": "20180102"}),
        ("daily", "全市场 20200102", {"trade_date": "20200102"}),
        ("daily", "全市场 20220104", {"trade_date": "20220104"}),
        ("daily", "全市场 20240102", {"trade_date": "20240102"}),
        ("daily", f"全市场 {ltd}", {"trade_date": ltd}),
        # 复权因子 / 每日指标 / 涨跌停价
        ("adj_factor", "全市场 20050104", {"trade_date": "20050104"}),
        ("adj_factor", f"全市场 {ltd}", {"trade_date": ltd}),
        ("daily_basic", "全市场 20050104", {"trade_date": "20050104"}),
        ("daily_basic", "全市场 20100104", {"trade_date": "20100104"}),
        ("daily_basic", "全市场 20150105", {"trade_date": "20150105"}),
        ("daily_basic", f"全市场 {ltd}", {"trade_date": ltd}),
        ("stk_limit", "全市场 20150105", {"trade_date": "20150105"}),
        ("stk_limit", "全市场 20200102", {"trade_date": "20200102"}),
        ("stk_limit", f"全市场 {ltd}", {"trade_date": ltd}),
        # 涨跌停清单
        ("limit_list_d", "全市场 20070104", {"trade_date": "20070104"}),
        ("limit_list_d", "全市场 20100104", {"trade_date": "20100104"}),
        ("limit_list_d", "全市场 20150105", {"trade_date": "20150105"}),
        ("limit_list_d", "全市场 20180102", {"trade_date": "20180102"}),
        ("limit_list_d", "全市场 20190102", {"trade_date": "20190102"}),
        ("limit_list_d", "全市场 20200102", {"trade_date": "20200102"}),
        ("limit_list_d", "全市场 20220104", {"trade_date": "20220104"}),
        ("limit_list_d", f"全市场 {ltd}", {"trade_date": ltd}),
        # 资金流 / 两融 / 龙虎榜 / 北向
        ("moneyflow", "全市场 20100104", {"trade_date": "20100104"}),
        ("moneyflow", "全市场 20150105", {"trade_date": "20150105"}),
        ("moneyflow", f"全市场 {ltd}", {"trade_date": ltd}),
        ("margin", "全市场 20100331", {"trade_date": "20100331"}),
        ("margin", "全市场 20150105", {"trade_date": "20150105"}),
        ("margin", f"全市场 {ltd}", {"trade_date": ltd}),
        ("margin_detail", "全市场 20100331", {"trade_date": "20100331"}),
        ("margin_detail", "全市场 20150105", {"trade_date": "20150105"}),
        ("margin_detail", f"全市场 {ltd}", {"trade_date": ltd}),
        ("top_list", "全市场 20100104", {"trade_date": "20100104"}),
        ("top_list", "全市场 20150105", {"trade_date": "20150105"}),
        ("top_list", f"全市场 {ltd}", {"trade_date": ltd}),
        ("hk_hold", "全市场 20170103", {"trade_date": "20170103"}),
        ("hk_hold", "全市场 20200102", {"trade_date": "20200102"}),
        ("hk_hold", f"全市场 {ltd}", {"trade_date": ltd}),
        # 指数
        ("index_daily", "000001.SH 1990-1992", {"ts_code": "000001.SH", "start_date": "19901219", "end_date": "19921231"}),
        ("index_daily", "000001.SH 2005-01", {"ts_code": "000001.SH", "start_date": "20050101", "end_date": "20050131"}),
        # 技术因子 / 筹码
        ("stk_factor", "全市场 20060104", {"trade_date": "20060104"}),
        ("stk_factor", "全市场 20100104", {"trade_date": "20100104"}),
        ("stk_factor", f"全市场 {ltd}", {"trade_date": ltd}),
        ("cyq_perf", "全市场 20180102", {"trade_date": "20180102"}),
        ("cyq_perf", "全市场 20190102", {"trade_date": "20190102"}),
        ("cyq_perf", f"全市场 {ltd}", {"trade_date": ltd}),
        # 分钟线
        ("stk_mins", "000001.SZ 5min 20180102", {"ts_code": S, "freq": "5min", "start_date": "20180102 09:30:00", "end_date": "20180102 15:00:00"}),
        ("stk_mins", "000001.SZ 5min 20200102", {"ts_code": S, "freq": "5min", "start_date": "20200102 09:30:00", "end_date": "20200102 15:00:00"}),
        ("stk_mins", "000001.SZ 5min 20220104", {"ts_code": S, "freq": "5min", "start_date": "20220104 09:30:00", "end_date": "20220104 15:00:00"}),
        ("stk_mins", "000001.SZ 5min 20240102", {"ts_code": S, "freq": "5min", "start_date": "20240102 09:30:00", "end_date": "20240102 15:00:00"}),
        ("stk_mins", "000001.SZ 5min 近30天窗口", {"ts_code": S, "freq": "5min", "start_date": f"{D30} 09:30:00", "end_date": f"{ltd} 15:00:00"}),
        ("stk_mins", "000001.SZ 1min 近5天", {"ts_code": S, "freq": "1min",
                                              "start_date": (date.today() - timedelta(days=7)).strftime("%Y%m%d") + " 09:30:00",
                                              "end_date": f"{ltd} 15:00:00"}),
        # 财务 / 股本 / 停复牌
        ("fina_indicator", "000001.SZ 全历史", {"ts_code": S}),
        ("fina_indicator_vip", "period=20250630", {"period": "20250630"}),
        ("share_float", "000001.SZ 全历史", {"ts_code": S}),
        ("stk_holdernumber", "000001.SZ 全历史", {"ts_code": S}),
        ("dividend", "000001.SZ 全历史", {"ts_code": S}),
        ("suspend_d", "20150105-20150109", {"start_date": "20150105", "end_date": "20150109"}),
        ("stock_basic", "全市场清单", {"list_status": "L"}),
        ("namechange", "000001.SZ 全历史", {"ts_code": S}),
    ]
    out = []
    for api, label, params in cases:
        rec = call(api, params, "primary", timeout=(10.0, 120.0))
        rec["label"] = label
        out.append(rec)
        print(f"{label:32s} {api:18s} {rec['status']:12s} rows={rec.get('rows', 0):<6} "
              f"{rec.get('first_date') or '-':>8s}..{rec.get('latest') or '-':<8s} {rec.get('ms', 0):>8.1f}ms "
              f"{(rec.get('error') or '')[:60]}", flush=True)
    return out


def _stat(lat: list[float]) -> dict:
    if not lat:
        return {}
    lat = sorted(lat)
    p95 = lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))]
    return {
        "n": len(lat),
        "min_ms": round(lat[0], 1),
        "p50_ms": round(statistics.median(lat), 1),
        "p95_ms": round(p95, 1),
        "max_ms": round(lat[-1], 1),
        "avg_ms": round(sum(lat) / len(lat), 1),
    }


def _concurrent(n: int, workers: int, api: str, params: dict, token_key: str = "primary") -> dict:
    t0 = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(call, api, params, token_key) for _ in range(n)]
        for f in futs:
            results.append(f.result())
    elapsed = time.perf_counter() - t0
    ok = [r for r in results if r.get("status") == "ok"]
    lat = [r["ms"] for r in ok]
    errs: dict[str, int] = {}
    for r in results:
        if r.get("status") != "ok":
            key = r.get("status", "unknown")
            errs[key] = errs.get(key, 0) + 1
    return {
        "requests": n,
        "workers": workers,
        "elapsed_s": round(elapsed, 2),
        "success": len(ok),
        "qps": round(len(ok) / elapsed, 2) if elapsed > 0 else 0,
        "errors": errs,
        "latency": _stat(lat),
        "error_samples": [r.get("error") for r in results if r.get("status") != "ok"][:3],
    }


def phase_perf(ltd: str, include_backup: bool = True) -> dict:
    D30 = (date.today() - timedelta(days=30)).strftime("%Y%m%d")
    light = {"ts_code": S, "start_date": D30, "end_date": ltd}
    heavy = {"trade_date": ltd}
    out: dict = {}

    # 预热（冷启动）单独记录
    out["cold_start"] = [call("daily", light, "primary") for _ in range(2)]
    # 串行轻查询
    serial_light = [call("daily", light, "primary") for _ in range(20)]
    out["serial_light"] = {"latency": _stat([r["ms"] for r in serial_light if r["status"] == "ok"]),
                           "errors": [r.get("error") for r in serial_light if r.get("status") != "ok"]}
    # 串行全市场日线（生产批量形态）
    serial_heavy = [call("daily", heavy, "primary", timeout=(10.0, 120.0)) for _ in range(6)]
    out["serial_heavy"] = {
        "latency": _stat([r["ms"] for r in serial_heavy if r["status"] == "ok"]),
        "rows": [r.get("rows") for r in serial_heavy],
        "bytes": [r.get("bytes") for r in serial_heavy],
        "errors": [r.get("error") for r in serial_heavy if r.get("status") != "ok"],
    }
    # 并发
    out["concurrent_4x40"] = _concurrent(40, 4, "daily", light)
    out["concurrent_8x48"] = _concurrent(48, 8, "daily", light)
    out["concurrent_heavy_2x6"] = _concurrent(6, 2, "daily", heavy)
    print(json.dumps({k: out[k] for k in ("serial_light", "serial_heavy")}, ensure_ascii=False, indent=1), flush=True)
    print(json.dumps(out["concurrent_4x40"], ensure_ascii=False), flush=True)
    print(json.dumps(out["concurrent_8x48"], ensure_ascii=False), flush=True)
    print(json.dumps(out["concurrent_heavy_2x6"], ensure_ascii=False), flush=True)

    # 持续 60s：3 并发打 daily 轻查询，观察限流与稳定吞吐
    deadline = time.perf_counter() + 60.0
    cap = 400
    lock = threading.Lock()
    counter = {"n": 0}
    results: list[dict] = []

    def worker() -> None:
        while True:
            with lock:
                if counter["n"] >= cap or time.perf_counter() >= deadline:
                    return
                counter["n"] += 1
            rec = call("daily", light, "primary")
            with lock:
                results.append(rec)

    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - t0
    ok = [r for r in results if r.get("status") == "ok"]
    errs: dict[str, int] = {}
    for r in results:
        if r.get("status") != "ok":
            errs[r.get("status", "unknown")] = errs.get(r.get("status", "unknown"), 0) + 1
    out["sustained_60s"] = {
        "elapsed_s": round(elapsed, 1),
        "requests": len(results),
        "success": len(ok),
        "rpm": round(len(ok) / elapsed * 60, 1) if elapsed > 0 else 0,
        "qps": round(len(ok) / elapsed, 2) if elapsed > 0 else 0,
        "errors": errs,
        "latency": _stat([r["ms"] for r in ok]),
        "error_samples": [r.get("error") for r in results if r.get("status") != "ok"][:5],
    }
    print("sustained_60s:", json.dumps(out["sustained_60s"], ensure_ascii=False), flush=True)

    if include_backup and TOKENS["backup"]:
        backup_serial = [call("daily", light, "backup") for _ in range(10)]
        out["backup_serial_light"] = {
            "latency": _stat([r["ms"] for r in backup_serial if r["status"] == "ok"]),
            "errors": [r.get("error") for r in backup_serial if r.get("status") != "ok"],
        }
        out["backup_concurrent_4x24"] = _concurrent(24, 4, "daily", light, "backup")
        print("backup:", json.dumps(out["backup_serial_light"], ensure_ascii=False), flush=True)
        print("backup concurrent:", json.dumps(out["backup_concurrent_4x24"], ensure_ascii=False), flush=True)
    return out


def phase_mcp() -> dict:
    token = TOKENS["primary"]
    if not token:
        return {"status": "no_token"}
    url = f"{MCP_URL}?token={token}"
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}

    def parse(resp: requests.Response) -> dict | None:
        ctype = resp.headers.get("Content-Type", "")
        text = resp.text
        if "text/event-stream" in ctype:
            for line in text.splitlines():
                if line.startswith("data:"):
                    try:
                        return json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    out: dict = {"url": MCP_URL}
    try:
        t0 = time.perf_counter()
        r = requests.post(url, headers=headers, timeout=(10, 30), json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "sxdata-probe", "version": "1.0"}},
        })
        out["initialize"] = {"http_status": r.status_code, "ms": round((time.perf_counter() - t0) * 1000, 1)}
        body = parse(r)
        out["initialize"]["server"] = ((body or {}).get("result") or {}).get("serverInfo")
        session_id = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        r2 = requests.post(url, headers=headers, timeout=(10, 30), json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        out["initialized_notification"] = {"http_status": r2.status_code}
        t0 = time.perf_counter()
        r3 = requests.post(url, headers=headers, timeout=(10, 30), json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        body3 = parse(r3) or {}
        tools = ((body3.get("result") or {}).get("tools") or [])
        out["tools_list"] = {"http_status": r3.status_code, "ms": round((time.perf_counter() - t0) * 1000, 1),
                             "count": len(tools), "names": [t.get("name") for t in tools][:60]}
        print("mcp:", json.dumps(out, ensure_ascii=False), flush=True)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        print("mcp error:", out["error"], flush=True)
    return out


def phase_compare(ltd: str) -> dict:
    """与现有 provider 同参数对比（跳过 promax：research 默认不占用实时额度）。"""
    sys.path.insert(0, str(ROOT / "backend"))
    from app.services.tushare_client import _build_provider_router  # noqa: E402

    router, _ = _build_provider_router()
    D30 = (date.today() - timedelta(days=30)).strftime("%Y%m%d")
    cases = [
        ("daily-light", "daily", {"ts_code": S, "start_date": D30, "end_date": ltd}, 4),
        ("daily-market", "daily", {"trade_date": ltd}, 2),
        ("daily_basic-market", "daily_basic", {"trade_date": ltd}, 2),
        ("limit_list_d", "limit_list_d", {"trade_date": ltd}, 2),
        ("index_daily", "index_daily", {"ts_code": "000300.SH", "start_date": D30, "end_date": ltd}, 2),
        ("stk_mins", "stk_mins", {"ts_code": S, "freq": "5min",
                                  "start_date": f"{ltd} 09:30:00", "end_date": f"{ltd} 15:00:00"}, 1),
    ]
    providers = [p.name for p in router._providers.values() if p.name != "promax"]
    out: dict = {"providers": providers, "cases": []}
    for label, api, params, repeats in cases:
        row = {"label": label, "api": api, "providers": {}}
        for name in providers:
            lat: list[float] = []
            rows: list[int] = []
            latest: list[str | None] = []
            errors: list[str] = []
            for _ in range(repeats):
                t0 = time.perf_counter()
                try:
                    env = router.request_pinned(name, api, params=params, fields="")
                    ms = (time.perf_counter() - t0) * 1000
                    data = (env or {}).get("data") or {}
                    fs = list(data.get("fields") or [])
                    items = list(data.get("items") or [])
                    _, hi = _date_bounds(fs, items)
                    lat.append(ms)
                    rows.append(len(items))
                    latest.append(hi)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{type(exc).__name__}: {str(exc)[:120]}")
            row["providers"][name] = {"latency": _stat(lat), "rows": rows, "latest": latest, "errors": errors[:2]}
        out["cases"].append(row)
        print("compare", json.dumps(row, ensure_ascii=False), flush=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all", choices=["all", "sweep", "range", "perf", "mcp", "compare"])
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--no-backup", action="store_true")
    ap.add_argument("--apis", default="", help="sweep 子集（逗号分隔），用于补测")
    a = ap.parse_args()

    if not TOKENS["primary"]:
        print("SXDATA_TOKEN 未设置", file=sys.stderr)
        return 2
    ltd = _last_trade_date()
    print(f"last_trade_date={ltd} url={API_URL}", flush=True)
    result: dict = {"probed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "base_url": API_URL, "last_trade_date": ltd}
    if a.phase in ("all", "sweep"):
        apis_filter = [x.strip() for x in a.apis.split(",") if x.strip()] or None
        swept = phase_sweep(ltd, apis_filter)
        result["sweep"] = swept
    if a.phase in ("all", "range"):
        result["range"] = phase_range(ltd)
    if a.phase in ("all", "perf"):
        result["perf"] = phase_perf(ltd, include_backup=not a.no_backup)
    if a.phase in ("all", "mcp"):
        result["mcp"] = phase_mcp()
    if a.phase in ("all", "compare"):
        result["compare"] = phase_compare(ltd)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if a.phase == "all":
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        prev = {}
        if out.exists():
            try:
                prev = json.loads(out.read_text())
            except json.JSONDecodeError:
                prev = {}
        # sweep 补测：按 api 合并，保留上一轮其它接口结果
        if a.phase == "sweep" and isinstance(prev.get("sweep"), list):
            merged = {r.get("api"): r for r in prev["sweep"]}
            for r in result["sweep"]:
                merged[r.get("api")] = r
            result["sweep"] = list(merged.values())
        prev.update(result)
        prev["probed_at"] = result["probed_at"]
        out.write_text(json.dumps(prev, ensure_ascii=False, indent=1))
    print("written:", out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
