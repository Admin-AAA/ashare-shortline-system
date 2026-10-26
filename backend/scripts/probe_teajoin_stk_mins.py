"""teajoin stk_mins（历史分钟）小量实测。严格限速、顺序请求；密钥只从 Settings 读取，不打印/不落盘。

  PYTHONPATH=. .venv/bin/python scripts/probe_teajoin_stk_mins.py <case> [...]
结果（脱敏摘要）打印到 stdout，由调用者决定是否写入 docs/teajoin_stk_mins_probe.json。
"""
from __future__ import annotations

import json
import sys
import time

import requests

from app.core.config import get_settings

SLEEP = 3.0  # 每次请求之间至少间隔（≈20 次/分，远低于 500/分上限，且不挤占实盘/补数）


def call(api: str, params: dict, fields: str = "", timeout: float = 60.0) -> dict:
    st = get_settings()
    url, token = st.tushare_teajoin_url.strip(), st.tushare_teajoin_token.strip()
    t0 = time.perf_counter()
    try:
        r = requests.post(url, json={"api_name": api, "token": token, "params": params, "fields": fields},
                          timeout=timeout, proxies={"http": "", "https": ""})
        ms = round((time.perf_counter() - t0) * 1000)
        hdr = {k: v for k, v in r.headers.items() if k.lower() in ("retry-after",)}
        try:
            j = r.json()
        except Exception:  # noqa: BLE001
            return {"http": r.status_code, "ms": ms, "hdr": hdr, "error": "non-json: " + r.text[:150].replace(token, "***")}
        return {"http": r.status_code, "ms": ms, "hdr": hdr, "json": j}
    except Exception as exc:  # noqa: BLE001
        return {"http": None, "ms": round((time.perf_counter() - t0) * 1000),
                "error": f"{type(exc).__name__}: {str(exc)[:150]}".replace(token, "***")}


def frame(res: dict):
    import pandas as pd

    j = res.get("json") or {}
    d = j.get("data") or {}
    return pd.DataFrame(d.get("items") or [], columns=d.get("fields") or [])


def brief(res: dict) -> dict:
    j = res.get("json") or {}
    out = {"http": res.get("http"), "ms": res.get("ms"), "code": j.get("code"), "msg": (j.get("msg") or "")[:120],
           "retry_after": res.get("hdr", {}).get("Retry-After")}
    if "error" in res:
        out["error"] = res["error"]
    if j.get("data"):
        df = frame(res)
        out["rows"] = len(df)
        out["fields"] = list(df.columns)
        tcol = next((c for c in ("trade_time", "datetime") if c in df.columns), None)
        if tcol and len(df):
            out["first"], out["last"] = str(df[tcol].min()), str(df[tcol].max())
    return out


if __name__ == "__main__":
    cases = json.loads(sys.argv[1])
    for c in cases:
        res, tries = {}, 0
        for tries in range(1, 4):  # teajoin 偶发 403 "invalid api key"（瞬时抖动）：探测里最多重试 3 次并记录次数
            res = call(c.get("api", "stk_mins"), c["params"])
            if (res.get("json") or {}).get("msg") != "invalid api key" and res.get("http") not in (500, None):
                break
            time.sleep(6)
        print(json.dumps({"case": c.get("name"), "tries": tries, **brief(res)}, ensure_ascii=False), flush=True)
        time.sleep(SLEEP)
