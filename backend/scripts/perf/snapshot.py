#!/usr/bin/env python3
"""把一组 GET 接口的返回存成规范化 JSON（键排序），用于改前改后逐字节比对。

用法：python3 snapshot.py --base http://127.0.0.1:8791 --out snap_base
     python3 snapshot.py --diff snap_base snap_perf
易变字段（每次请求都会变的时间戳等）在 VOLATILE 里列出，比对前替换为 "<volatile>"。
"""
import argparse
import hashlib
import json
import pathlib
import sys
import urllib.parse
import urllib.request

URLS = [
    "/api/recommendations/history?limit=200",
    "/api/recommendations/history?limit=200&offset=200",
    "/api/recommendations/history?limit=200&offset=400",
    "/api/recommendations/history?limit=50",
    "/api/recommendations/history?limit=50&offset=50",
    "/api/recommendations/history?limit=50&label=success",
    "/api/recommendations/history?limit=50&label=pending",
    "/api/recommendations/history?limit=50&label=missed",
    "/api/recommendations/history?limit=50&date_from=2026-09-01&date_to=2026-10-31",
    "/api/recommendations/history?limit=50&code=600000",
    "/api/runs?limit=100&fields=slim",
    "/api/records/runs?page=1&page_size=20&fields=slim",
    "/api/records/runs?page=2&page_size=20&fields=slim",
    "/api/records/runs?page=1&page_size=20",
    "/api/records/runs?limit=100",
    "/api/records/runs?page=1&page_size=20&fields=slim&min_picks=1&sort=picks&order=desc",
    "/api/dashboard/overview",
    "/api/strategies",
    "/api/strategies/standalone-metrics",
    "/api/stats/by-preset",
]
for h in ("mid", "long"):
    URLS += [
        f"/api/horizons/{h}/strategies",
        f"/api/horizons/{h}/picks?limit=60",
        f"/api/horizons/{h}/picks?limit=30",
        f"/api/horizons/{h}/picks?page=1&page_size=20",
        f"/api/horizons/{h}/picks?page=2&page_size=20",
        f"/api/horizons/{h}/picks?page=1&page_size=200",
        f"/api/horizons/{h}/picks?page=2&page_size=200",
        f"/api/horizons/{h}/picks?page=3&page_size=200",
        f"/api/horizons/{h}/trades",
        f"/api/horizons/{h}/trades?origin=live",
        f"/api/horizons/{h}/trades?origin=replay",
        f"/api/horizons/{h}/metrics?origin=all",
        f"/api/horizons/{h}/metrics?origin=live",
        f"/api/horizons/{h}/metrics?origin=all&window_days=90",
        f"/api/horizons/{h}/runs?limit=60",
    ]

# 每次请求本就会变的字段（键名）。dashboard 的生成时间、请求 id 等。
VOLATILE = {"generated_at", "server_time", "now", "as_of_time", "request_id", "elapsed_ms", "computed_at",
            "cache_age_seconds", "checked_at", "fetched_at", "heartbeat_age_seconds"}


def scrub(x):
    if isinstance(x, dict):
        return {k: ("<volatile>" if k in VOLATILE else scrub(v)) for k, v in x.items()}
    if isinstance(x, list):
        return [scrub(v) for v in x]
    return x


def fname(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:10] + "_" + urllib.parse.quote(url, safe="")[:80] + ".json"


def snap(base: str, out: str) -> None:
    d = pathlib.Path(out)
    d.mkdir(parents=True, exist_ok=True)
    for u in URLS:
        with urllib.request.urlopen(base + u, timeout=180) as r:
            data = json.loads(r.read())
        (d / fname(u)).write_text(json.dumps(scrub(data), ensure_ascii=False, sort_keys=True, indent=0))
    print(f"saved {len(URLS)} -> {d}")


def diff(a: str, b: str) -> int:
    bad = 0
    for u in URLS:
        fa, fb = pathlib.Path(a) / fname(u), pathlib.Path(b) / fname(u)
        if fa.read_bytes() != fb.read_bytes():
            bad += 1
            la, lb = fa.read_text().splitlines(), fb.read_text().splitlines()
            first = next((i for i, (x, y) in enumerate(zip(la, lb)) if x != y), min(len(la), len(lb)))
            print(f"DIFF {u}\n   line {first}: {la[first][:160] if first < len(la) else '<eof>'}\n   vs       {lb[first][:160] if first < len(lb) else '<eof>'}")
    print(f"{len(URLS) - bad}/{len(URLS)} identical")
    return bad


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base")
    ap.add_argument("--out")
    ap.add_argument("--diff", nargs=2)
    a = ap.parse_args()
    if a.diff:
        sys.exit(1 if diff(*a.diff) else 0)
    snap(a.base.rstrip("/"), a.out)
