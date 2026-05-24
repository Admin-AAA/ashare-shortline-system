"""Tushare K线批量拉取 — 存为JSON文件，不连MySQL，避免后台挂起问题。"""

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

TUSHARE_URL = "http://101.35.233.113:8020"
TUSHARE_TOKEN = "f8b05f8ee54f468220f2569afe56b4c027f1d388d164899339d394ad"
BATCH_SIZE = 200
SLEEP = 0.25
OUT_DIR = Path("/tmp/ashare_kbars_json")

session = requests.Session()


def fetch(code):
    suffix = "SH" if code.startswith(("6", "9")) else "SZ"
    sd = (date.today() - timedelta(days=750)).strftime("%Y%m%d")
    ed = date.today().strftime("%Y%m%d")
    for attempt in range(3):
        try:
            r = session.post(
                f"{TUSHARE_URL}/daily",
                json={"api_name": "daily", "token": TUSHARE_TOKEN,
                      "params": {"ts_code": f"{code}.{suffix}", "start_date": sd, "end_date": ed},
                      "fields": ""},
                timeout=(5, 10))
            r.raise_for_status()
            d = r.json()
            if d["code"] != 0:
                return None
            items = d.get("data", {}).get("items", [])
            if not items:
                return []
            # fields: [ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount]
            items = sorted(items, key=lambda x: x[1])[-250:]
            return items
        except Exception:
            if attempt < 2:
                time.sleep(1 + attempt)
    return None


def main():
    codes_file = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/kbar_codes_needed.txt")
    codes = [l.strip() for l in codes_file.read_text().splitlines() if l.strip()]
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    existing_batches = {p.stem for p in out_dir.glob("batch_*.json")}
    total_ok = 0
    total_fail = 0
    t_start = time.monotonic()

    for bi in range(0, len(codes), BATCH_SIZE):
        batch_name = f"batch_{bi // BATCH_SIZE:04d}"
        out_file = out_dir / f"{batch_name}.json"
        if batch_name in existing_batches:
            existing_batches.discard(batch_name)
            continue

        batch = codes[bi:bi + BATCH_SIZE]
        bt0 = time.monotonic()
        batch_data = []
        ok = 0
        fail = 0

        for code in batch:
            items = fetch(code)
            if items is not None:
                batch_data.append({"code": code, "items": items})
                ok += 1
            else:
                fail += 1
            time.sleep(SLEEP)

        if batch_data:
            out_file.write_text(json.dumps(batch_data, ensure_ascii=False))

        total_ok += ok
        total_fail += fail
        elapsed = time.monotonic() - bt0
        total_elapsed = time.monotonic() - t_start
        pct = (bi + len(batch)) / len(codes) * 100
        msg = (f"[{bi//BATCH_SIZE+1}/{ (len(codes)+BATCH_SIZE-1)//BATCH_SIZE}] "
               f"ok={ok} fail={fail} {elapsed:.1f}s | 累计 ok={total_ok} fail={total_fail} "
               f"进度 {min(pct,100):.1f}% {total_elapsed:.0f}s")
        print(msg, flush=True)

    session.close()
    print(f"完成! ok={total_ok} fail={total_fail} 耗时 {time.monotonic()-t_start:.0f}s", flush=True)


if __name__ == "__main__":
    main()
