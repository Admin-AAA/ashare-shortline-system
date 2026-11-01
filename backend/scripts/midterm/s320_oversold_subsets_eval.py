"""§320 评估：超跌资金反包强信号子集 vs BASE，按预注册 7 条判定。只读 output/research/s320/subsets_trades.json。
用法：cd backend && .venv/bin/python scripts/midterm/s320_oversold_subsets_eval.py"""
from __future__ import annotations
import json, sys
from pathlib import Path
B = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(B)); sys.path.insert(0, str(B / "scripts/midterm"))
from s310_short_combo_compare import metrics  # noqa: E402
import numpy as np  # noqa: E402

T = json.loads((B / "output/research/s320/subsets_trades.json").read_text())
WIN = {"R12": ["12m"], "N3": ["12m", "EXT"], "EXT": ["EXT"], "W0": ["W0"], "W00": ["W00"]}


def hd(t):
    a, b = t["en"], t["ex"]
    return max(1, int(np.busday_count(f"{a[:4]}-{a[4:6]}-{a[6:]}", f"{b[:4]}-{b[4:6]}-{b[6:]}")))


out = {}
base = {w: metrics([t for p in WIN[w] for t in T["BASE"][p]]) for w in ("R12", "N3")}
for v in T:
    r = {}
    for w in WIN:
        ts = [t for p in WIN[w] for t in T[v][p]]
        if not ts: r[w] = None; continue
        m = metrics(ts); m["hold"] = round(float(np.mean([hd(t) for t in ts])), 2); m["per_day"] = round(m["avg"] / m["hold"], 2)
        r[w] = m
    R, N = r["R12"], r["N3"]
    c = {}
    if R and N:
        c["1_avg_pos"] = R["avg"] > 0 and N["avg"] > 0
        c["2_pf"] = R["pf"] >= 1.3 and N["pf"] >= 1.3
        c["3_mdd"] = R["mdd"] >= -35 and N["mdd"] >= -35
        pos = sum(1 for w in ("EXT", "R12", "W0", "W00") if r[w] and r[w]["avg"] > 0)
        c["4_phases"] = pos >= 3 and all(r[w] and r[w]["avg"] >= -5 for w in ("W0", "W00"))
        c["5_n"] = R["n"] >= 30 and N["n"] >= 30
        c["6_robust"] = all((x["trim_both5"] if x["trim_both5"] is not None else -1) > 0 and x["median"] > 0 for x in (R, N))
        c["7_better_than_base"] = R["avg"] >= base["R12"]["avg"] + 0.5 and N["avg"] >= base["N3"]["avg"] + 0.5
        r["pos_windows"] = pos
    else:
        c["5_n"] = False
    r["criteria"] = c; r["pass"] = len(c) == 7 and all(c.values())
    out[v] = r
    print(v, "PASS" if r["pass"] else "FAIL", "".join("✓" if x else "✗" for x in c.values()), c)
    for w in ("R12", "N3", "EXT", "W0", "W00"):
        if r[w]: print("   ", w, {k: r[w][k] for k in ("n", "avg", "pf", "mdd", "streak", "median", "trim_both5", "top10_share", "hold", "per_day", "signal_days")})
(B / "output/research/s320/eval.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float))
