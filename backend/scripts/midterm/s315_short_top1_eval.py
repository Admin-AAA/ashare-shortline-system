"""§315-F：短线 TOP1 逐技能评估（vs 同框架 Top3 基线）。只读 output/research/s315/short_top1_trades.json 与 s282 short_trades_base.json。
用法：cd backend && .venv/bin/python scripts/midterm/s315_short_top1_eval.py"""
from __future__ import annotations
import json, sys
from pathlib import Path
B = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(B)); sys.path.insert(0, str(B / "scripts/midterm"))
from s310_short_combo_compare import metrics  # noqa: E402
from s309_excess_hs300 import SHORT_SKILLS  # noqa: E402

T1 = json.loads((B / "output/research/s315/short_top1_trades.json").read_text())
T3 = json.loads((B / "output/research/s282/short_trades_base.json").read_text())
WIN = {"R12": ["12m"], "N3": ["12m", "EXT"], "EXT": ["EXT"], "W0": ["W0"], "W00": ["W00"]}


def sel(t, w, sid): return [x for p in WIN[w] for x in t[p] if x["sid"] == sid]


out = {}
for sid in SHORT_SKILLS:
    r = {}
    for w in WIN:
        a, b = sel(T1, w, sid), sel(T3, w, sid)
        if not a: r[w] = None; continue
        m1 = metrics(a); m3 = metrics(b) if b else None
        k3 = {(x["dt"], x["code"]) for x in b}
        m1["overlap_top3"] = round(sum(1 for x in a if (x["dt"], x["code"]) in k3) / len(a), 3)
        r[w] = {"top1": m1, "top3": m3}
    c = {}
    R, N = r["R12"], r["N3"]
    if R and N:
        c["1_avg_pos"] = R["top1"]["avg"] > 0 and N["top1"]["avg"] > 0
        c["2_pf"] = R["top1"]["pf"] >= 1.3 and N["top1"]["pf"] >= 1.3
        c["3_mdd"] = R["top1"]["mdd"] >= -35 and N["top1"]["mdd"] >= -35
        pos = sum(1 for w in ("EXT", "R12", "W0", "W00") if r[w] and r[w]["top1"]["avg"] > 0)
        c["4_phases"] = pos >= 3 and all(r[w] and r[w]["top1"]["avg"] >= -5 for w in ("W0", "W00"))
        c["5_n"] = R["top1"]["n"] >= 30 and N["top1"]["n"] >= 30
        c["6_robust"] = all((x["top1"]["trim_both5"] or -1) > 0 and x["top1"]["median"] > 0 for x in (R, N))
        c["7_better_than_top3"] = all(x["top1"]["avg"] >= x["top3"]["avg"] + 0.5 and x["top1"]["mdd"] >= x["top3"]["mdd"] for x in (R, N))
        r["pos_windows"] = pos
    else:
        c["5_n"] = False
    r["criteria"] = c; r["pass"] = all(c.values()) and len(c) == 7
    out[sid] = r
    print(sid, "PASS" if r["pass"] else "FAIL", c)
    for w in ("R12", "N3", "EXT", "W0", "W00"):
        if r[w]: print("  ", w, "top1", {k: r[w]["top1"][k] for k in ("n", "avg", "pf", "mdd", "streak", "median", "trim_both5", "top10_share", "overlap_top3")}, "| top3", {k: r[w]["top3"][k] for k in ("n", "avg", "pf", "mdd")} if r[w]["top3"] else None)
(B / "output/research/s315/short_top1_eval.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float))
