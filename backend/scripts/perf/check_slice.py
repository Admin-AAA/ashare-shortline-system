"""核对前端改法：全量 200/页拼接后按 20 切片 == 后端 page_size=20 分页（含 total / total_picks / has_more）。
用法：python3 check_slice.py http://127.0.0.1:8792"""
import json
import sys
import urllib.request

B = sys.argv[1]


def g(u):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(B + u, headers={"X-Ashare-Perf-Probe": "1"}), timeout=120).read())


ok = n = 0
for h in ("mid", "long"):
    sids = [None] + [s["id"] for s in g(f"/api/horizons/{h}/strategies")["strategies"]][:3]
    for sid in sids:
        q = f"&strategy_id={sid}" if sid else ""
        first = g(f"/api/horizons/{h}/picks?page=1&page_size=200{q}")
        pages = [first] + [g(f"/api/horizons/{h}/picks?page={i}&page_size=200{q}") for i in range(2, -(-first["total"] // 200) + 1)]
        allb = [b for p in pages for b in p["batches"]]
        for page in range(1, max(1, -(-first["total"] // 20)) + 1):
            srv = g(f"/api/horizons/{h}/picks?page={page}&page_size=20{q}")
            mine = {"batches": allb[(page - 1) * 20: page * 20], "total": first["total"],
                    "total_picks": first["total_picks"], "has_more": page * 20 < first["total"]}
            n += 1
            ok += all(json.dumps(srv[k], sort_keys=True) == json.dumps(mine[k], sort_keys=True) for k in mine)
print(f"page slices identical {ok}/{n}")
