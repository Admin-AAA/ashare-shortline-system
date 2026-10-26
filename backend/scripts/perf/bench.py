#!/usr/bin/env python3
"""A 股系统读接口性能基准（只发 GET，不触发写入）。量「200ms 内打开」里程碑用。

本目录脚本（都只读）：
  bench.py          接口热测 p50/p95、达标个数、三个复盘页整页热/冷耗时
  first_hit.py      后端刚启动（缓存全空）时三个复盘页第一次打开的耗时
  snapshot.py       抓一组接口返回存成规范化 JSON，改前改后逐字节比对
  check_realize.py  核对中长线批量结算 == 逐只单查（需在 backend/ 下用 .venv 跑）
  check_slice.py    核对中长线复盘前端切片 == 后端分页
  baseline.json     2026-10-26 改造前基线（bench.py --cold 3 --json 的输出）

用法（在 backend/scripts/perf/ 下）：
  python3 bench.py                 # 热测：每个接口 N 次，出 p50/p95 + 达标占比 + 三个复盘页整页
  python3 bench.py --cold 3        # 另外测 3 轮「冷」复盘页（每轮前空闲 31 秒，让 30s 缓存过期）
  python3 bench.py --json out.json # 结果另存 JSON，方便和 baseline.json 对比
  python3 first_hit.py http://127.0.0.1:8792
  python3 snapshot.py --base http://127.0.0.1:8791 --out snap_a ; python3 snapshot.py --diff snap_a snap_b
  cd ../.. && PYTHONPATH=. .venv/bin/python scripts/perf/check_realize.py
参数：--base http://127.0.0.1:8765  --runs 7（热测次数）  --budget 200（毫秒）
只用标准库；请求带 gzip 头，和浏览器一致；串行发送，页面流按前端真实并发。
量改动前后时，可以在 worktree 里起只读临时后端：
  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8792 --lifespan off
（--lifespan off：不迁移、不注册定时任务、不起预热线程；注意它没有主后端的启动预热。）
"""
import argparse
import json
import statistics
import threading
import time
import urllib.request

# 主要读接口：页面 -> 首屏会调的 GET 接口（和 frontend-v2 一致）
ENDPOINTS = [
    ("短线 今日", "/api/runs?limit=100&fields=slim"),
    ("短线 复盘", "/api/recommendations/history?limit=200"),
    ("短线 复盘", "/api/recommendations/history?limit=200&offset=200"),
    ("短线 复盘", "/api/recommendations/history?limit=200&offset=400"),
    ("短线 交易明细", "/api/recommendations/history?limit=50"),
    ("短线 运行记录", "/api/records/runs?page=1&page_size=20&fields=slim"),
    ("短线 运行记录", "/api/presets"),
    ("短线 策略", "/api/strategies"),
    ("短线 策略", "/api/stats/by-preset"),
    ("短线 策略", "/api/strategies/standalone-metrics"),
    ("短线 驾驶舱", "/api/dashboard/overview"),
    ("短线 持仓", "/api/recommendations/realtime-pnl"),
    ("系统", "/api/admin/data-health"),
    ("系统", "/api/admin/data-quality"),
    ("系统", "/api/admin/data-health/remediate/status"),
]
for h, name in (("mid", "中线"), ("long", "长线")):
    ENDPOINTS += [
        (f"{name} 今日/策略", f"/api/horizons/{h}/strategies"),
        (f"{name} 今日/策略", f"/api/horizons/{h}/picks?limit=60"),
        (f"{name} 复盘", f"/api/horizons/{h}/picks?page=1&page_size=20"),
        (f"{name} 复盘", f"/api/horizons/{h}/picks?page=1&page_size=200"),
        (f"{name} 交易明细", f"/api/horizons/{h}/trades"),
        (f"{name} 运行记录", f"/api/horizons/{h}/runs?limit=60"),
        (f"{name} 诊断", f"/api/horizons/{h}/metrics?origin=all"),
    ]

BASE = "http://127.0.0.1:8765"


def get(path: str) -> tuple[float, int, int]:
    req = urllib.request.Request(BASE + path, headers={"Accept-Encoding": "gzip"})
    t = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read()
        code = r.status
    return (time.perf_counter() - t) * 1000, code, len(body)


def parallel(paths: list[str]) -> None:
    ths = [threading.Thread(target=get, args=(p,)) for p in paths]
    for t in ths:
        t.start()
    for t in ths:
        t.join()


def _horizon_review(h: str) -> None:
    """中长线复盘页：策略 + 最近60批 + 分页20 + 全量200（按 total 翻页），同时发出。"""
    first = {}

    def full():
        req = urllib.request.Request(f"{BASE}/api/horizons/{h}/picks?page=1&page_size=200")
        with urllib.request.urlopen(req, timeout=120) as r:
            first.update(json.loads(r.read()))
        n = -(-int(first.get("total") or 0) // 200)
        parallel([f"/api/horizons/{h}/picks?page={i}&page_size=200" for i in range(2, n + 1)])

    ths = [threading.Thread(target=get, args=(p,)) for p in (
        f"/api/horizons/{h}/strategies", f"/api/horizons/{h}/picks?limit=60",
        f"/api/horizons/{h}/picks?page=1&page_size=20")] + [threading.Thread(target=full)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()


def _short_review() -> None:
    """短线复盘页：先取第 1 页拿 total，再并发取其余页（上限 2000 条）。"""
    req = urllib.request.Request(f"{BASE}/api/recommendations/history?limit=200")
    with urllib.request.urlopen(req, timeout=120) as r:
        total = min(int(json.loads(r.read()).get("total") or 0), 2000)
    parallel([f"/api/recommendations/history?limit=200&offset={o}" for o in range(200, total, 200)])


PAGES = {"短线复盘": _short_review, "中线复盘": lambda: _horizon_review("mid"), "长线复盘": lambda: _horizon_review("long")}


def timed(fn) -> float:
    t = time.perf_counter()
    fn()
    return (time.perf_counter() - t) * 1000


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    if len(xs) == 1:
        return xs[0]
    return statistics.quantiles(xs, n=100, method="inclusive")[int(q) - 1]


def main() -> None:
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--runs", type=int, default=7)
    ap.add_argument("--cold", type=int, default=0, help="冷测复盘页轮数（每轮前空闲 31 秒）")
    ap.add_argument("--budget", type=float, default=200.0)
    ap.add_argument("--json")
    a = ap.parse_args()
    BASE = a.base.rstrip("/")
    out: dict = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "budget_ms": a.budget, "endpoints": [], "pages": {}}

    if a.cold:
        cold: dict[str, list[float]] = {k: [] for k in PAGES}
        for i in range(a.cold):
            for k, fn in PAGES.items():
                time.sleep(31)  # 让 30 秒短缓存过期
                cold[k].append(timed(fn))
        out["pages_cold"] = {k: {"p50": round(pct(v, 50)), "p95": round(pct(v, 95)), "samples": [round(x) for x in v]}
                             for k, v in cold.items()}

    for page, path in ENDPOINTS:
        get(path)  # 预热一次，不计入
        ts, size = [], 0
        for _ in range(a.runs):
            ms, code, size = get(path)
            ts.append(ms)
        out["endpoints"].append({"page": page, "path": path, "status": code, "p50": round(pct(ts, 50)),
                                 "p95": round(pct(ts, 95)), "kb_gzip": round(size / 1024, 1)})
    for k, fn in PAGES.items():
        fn()
        ts = [timed(fn) for _ in range(a.runs)]
        out["pages"][k] = {"p50": round(pct(ts, 50)), "p95": round(pct(ts, 95))}

    ok = sum(1 for e in out["endpoints"] if e["p95"] <= a.budget)
    out["share_p95_within_budget"] = f"{ok}/{len(out['endpoints'])}"
    review_first = [e for e in out["endpoints"] if "复盘" in e["page"]]
    out["review_endpoints_max_p95"] = max(e["p95"] for e in review_first)

    print(f"{'页面':<10} {'p50':>6} {'p95':>6} {'KB':>6}  接口")
    for e in out["endpoints"]:
        flag = "" if e["p95"] <= a.budget else "  <-- 超标"
        print(f"{e['page']:<10} {e['p50']:>6} {e['p95']:>6} {e['kb_gzip']:>6}  {e['path']}{flag}")
    print("\n整页（热）:", out["pages"])
    if "pages_cold" in out:
        print("整页（冷）:", out["pages_cold"])
    print(f"\n热 p95≤{a.budget:.0f}ms 的接口: {out['share_p95_within_budget']}")
    print(f"复盘页接口最大热 p95: {out['review_endpoints_max_p95']}ms")
    if a.json:
        with open(a.json, "w") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
