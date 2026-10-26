"""【已弃用，保留备查｜2026-10-26 20:1x】请改用 fetch_limit_list_d.py（走统一数据网关：限流/监控/额度统计/降级全在）。
网关现在对 teajoin 的「HTTP 429 query capacity busy」在批量进程里就地短退避重试（数秒、带抖动、不开熔断），直连版的存在理由已消失。

limit_list_d 2020 起回补（teajoin 直连版）：接续 fetch_limit_list_d.py 的 parquet（同目录、同列、同去重键），只走 teajoin。

为什么单独一个脚本：promax/quantdata 今日额度耗尽；teajoin 上游对「历史单日重查询」常返回 `query capacity busy`（多发于并发或连发），
网关熔断会把它放大成分钟级停摆。本脚本单路串行 + 容量忙就地短退避重试（5s×n，最多 8 次），不占 promax/quantdata 额度，也不碰 live 熔断。
盘中（工作日 09:15–15:05）自动暂停，给实盘让路。密钥只从 Settings(.env) 读取。
用法：cd backend && PYTHONPATH=. .venv/bin/python scripts/shortline_research/fetch_limit_list_d_teajoin.py [start=20200102] [end=最近交易日]
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from app.core.config import get_settings
from scripts.shortline_research.fetch_limit_list_d import OUT, intraday, merge_year

TZ = ZoneInfo("Asia/Shanghai")
SRC_TJ = "teajoin:limit_list_d"


def call(url: str, token: str, params: dict) -> tuple[list[str], list[list], str]:
    r = requests.post(url, json={"api_name": "limit_list_d", "token": token, "params": params, "fields": ""}, timeout=90,
                      proxies={"http": "", "https": ""})
    j = r.json()
    d = j.get("data") or {}
    return d.get("fields") or [], d.get("items") or [], str(j.get("msg") or "").replace(token, "***")


def fetch_day(url: str, token: str, day: str) -> pd.DataFrame | None:
    for attempt in range(8):
        try:
            fields, items, msg = call(url, token, {"trade_date": day})
        except Exception as exc:  # noqa: BLE001
            fields, items, msg = [], [], f"{type(exc).__name__}"
        if items or ("busy" not in msg and "Error" not in msg and "Timeout" not in msg and msg == ""):
            return pd.DataFrame(items, columns=fields)
        time.sleep(5 * (attempt + 1))
    return None


def main() -> None:
    st = get_settings()
    token, url = st.tushare_teajoin_token.strip(), st.tushare_teajoin_url.strip()
    if not token:
        raise SystemExit("teajoin token 未配置")
    start = sys.argv[1] if len(sys.argv) > 1 else "20200102"
    end = sys.argv[2] if len(sys.argv) > 2 else datetime.now(TZ).strftime("%Y%m%d")
    OUT.mkdir(parents=True, exist_ok=True)
    cal = None
    # 交易日历：用 trade_cal
    for attempt in range(6):
        r = requests.post(url, json={"api_name": "trade_cal", "token": token, "params": {"exchange": "SSE", "start_date": start, "end_date": end, "is_open": "1"}, "fields": ""},
                          timeout=90, proxies={"http": "", "https": ""}).json()
        d = r.get("data") or {}
        if d.get("items"):
            cal = pd.DataFrame(d["items"], columns=d["fields"])
            break
        time.sleep(5 * (attempt + 1))
    if cal is None:
        raise SystemExit("trade_cal 取不到")
    days = sorted(cal["cal_date"].astype(str))
    done: set[str] = set()
    for f in OUT.glob("limit_list_d_*.parquet"):
        done |= set(pd.read_parquet(f, columns=["trade_date"])["trade_date"].astype(str))
    empty_file = OUT / "_empty_days.txt"
    if empty_file.exists():
        done |= set(empty_file.read_text().split())
    todo = [d for d in days if d not in done]
    print(f"{len(days)} trade days, {len(todo)} to fetch", flush=True)
    buf: list[pd.DataFrame] = []
    fails = 0

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        new = pd.concat(buf, ignore_index=True)
        for y, part in new.groupby(new["trade_date"].astype(str).str[:4]):
            merge_year(OUT / f"limit_list_d_{y}.parquet", part)
        buf = []

    for i, d in enumerate(todo):
        while intraday():  # 盘中让路
            flush()
            time.sleep(60)
        t0 = time.time()
        df = fetch_day(url, token, d)
        if df is None:
            fails += 1
            print(d, "FAILED after retries", flush=True)
            if fails >= 6:
                print("too many consecutive failures, stop", flush=True)
                break
            time.sleep(60)
            continue
        fails = 0
        if len(df):
            df["trade_date"] = df["trade_date"].astype(str)
            df["src"] = SRC_TJ
            df["fetched_at"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
            buf.append(df)
        else:
            with open(empty_file, "a") as fh:
                fh.write(d + "\n")
        if (i + 1) % 20 == 0:
            flush()
            print(f"{i + 1}/{len(todo)} last={d} rows={len(df)}", flush=True)
        time.sleep(max(0.0, 2.0 - (time.time() - t0)))
    flush()
    print("done", flush=True)


if __name__ == "__main__":
    main()
