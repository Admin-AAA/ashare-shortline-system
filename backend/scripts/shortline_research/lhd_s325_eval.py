"""§325 评估：读 output/research/s325/trades.json，按预注册 8 条判定。只读。
用法：cd backend && .venv/bin/python scripts/shortline_research/lhd_s325_eval.py"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np
B = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(B)); sys.path.insert(0, str(B / "scripts/midterm"))
from s310_short_combo_compare import metrics  # noqa: E402
from s309_excess_hs300 import SHORT_SKILLS, SHORT_TRADES  # noqa: E402

OUT = B / "output/research/s325"
WIN = {"R12": ("20251001", "20261016"), "N3": ("20240101", "20261016"), "EXT": ("20240101", "20250930"),
       "W0": ("20220401", "20230331"), "W00": ("20230401", "20231231"), "Y2021": ("20210101", "20211231"),
       "Y2022Q1": ("20220101", "20220331"), "Y2024": ("20240101", "20241231"), "Y2025": ("20250101", "20251231")}
CRIT_WIN = ("EXT", "R12", "W0", "W00")


def sel(ts, w):
    a, b = WIN[w]
    return [t for t in ts if a <= t["en"] <= b]


def mm(ts):
    if not ts:
        return None
    m = metrics(ts)
    m["hold"] = round(float(np.mean([t["hold"] for t in ts])), 2)
    m["per_day"] = round(m["avg"] / m["hold"], 3)
    return m


def judge(r, ctrl, active_ref, overlap):
    R, N = r["R12"], r["N3"]
    c = {}
    if not (R and N):
        return {"5_n": False}
    c["1_avg_pos"] = R["avg"] > 0 and N["avg"] > 0
    c["2_pf"] = R["pf"] >= 1.3 and N["pf"] >= 1.3
    c["3_mdd"] = N["mdd"] >= -35
    pos = sum(1 for w in CRIT_WIN if r[w] and r[w]["avg"] > 0)
    c["4_windows"] = pos >= 3 and all(r[w] and r[w]["avg"] >= -5 for w in ("W0", "W00"))
    c["5_n"] = R["n"] >= 30 and N["n"] >= 30
    c["6_robust"] = all((x["trim_both5"] if x["trim_both5"] is not None else -1) > 0 and x["median"] > 0 for x in (R, N))
    c["7_beats_active_ctrl"] = N["avg"] > active_ref and R["avg"] > ctrl["R12"] and N["avg"] > ctrl["N3"]
    c["8_overlap"] = overlap["N3_share_max"] <= 0.5 or N["avg"] > overlap["N3_overlapped_skill_avg"]
    return c


def main():
    T = json.loads((OUT / "trades.json").read_text())
    act = json.loads(SHORT_TRADES.read_text())
    act_n3 = [t for p in ("12m", "EXT") for t in act[p]]
    act_keys = {s: {(t["en"], t["code"]) for t in act_n3 if t["sid"] == s} for s in SHORT_SKILLS}
    act_avg = {s: float(np.mean([t["net"] for t in act_n3 if t["sid"] == s])) for s in SHORT_SKILLS}
    act_all = {(t["en"], t["code"]) for t in act_n3}
    out = {"active_n3_avg": act_avg}
    for ex in ("n2_elc", "n1_elc", "n3_elc", "n2_fix"):
        res = {}
        # CTRL 平均
        cw = {}
        for w in ("R12", "N3"):
            xs = [mm(sel(T[f"CTRL{s}"][ex], w)) for s in (1, 2, 3)]
            cw[w] = float(np.mean([x["avg"] for x in xs]))
        res["CTRL"] = {"avg": cw, "pf": {w: round(float(np.mean([mm(sel(T[f"CTRL{s}"][ex], w))["pf"] for s in (1, 2, 3)])), 3) for w in ("R12", "N3")},
                       "n": {w: round(float(np.mean([len(sel(T[f"CTRL{s}"][ex], w)) for s in (1, 2, 3)])), 1) for w in ("R12", "N3")},
                       "windows": {w: round(float(np.mean([mm(sel(T[f"CTRL{s}"][ex], w))["avg"] for s in (1, 2, 3)])), 3) for w in CRIT_WIN if sel(T["CTRL1"][ex], w)}}
        for v in ("V1", "V2", "V3", "V4", "ALL"):
            ts = T[v][ex]
            r = {w: mm(sel(ts, w)) for w in WIN}
            n3 = sel(ts, "N3")
            keys = {(t["en"], t["code"]) for t in n3}
            ov = {s: round(len(keys & act_keys[s]) / max(len(keys), 1), 3) for s in SHORT_SKILLS}
            smax = max(ov, key=ov.get)
            overlap = {"N3_share_by_skill": ov, "N3_share_any": round(len(keys & act_all) / max(len(keys), 1), 3), "N3_share_max": ov[smax],
                       "N3_overlapped_skill": smax, "N3_overlapped_skill_avg": act_avg[smax]}
            r["overlap"] = overlap
            r["bought"] = {"signals_n3": sum(1 for t in ts if t["en"] >= "20240101"), "L_dist_N3": {str(k): int(sum(1 for t in n3 if min(t["L"], 4) == k)) for k in (1, 2, 3, 4)}}
            if ex == "n2_elc" and v != "ALL":
                r["criteria"] = judge(r, {"R12": cw["R12"], "N3": cw["N3"]}, min(act_avg.values()) if False else max(act_avg.values()), overlap)
                r["pass"] = len(r["criteria"]) == 8 and all(r["criteria"].values())
            res[v] = r
        out[ex] = res
    (OUT / "eval.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float))
    for ex in out:
        if ex == "active_n3_avg": continue
        print("==", ex)
        for v, r in out[ex].items():
            if v == "CTRL":
                print(" CTRL", r); continue
            line = " ".join(f"{w}:{r[w]['n']}/{r[w]['avg']:+.2f}/pf{r[w]['pf']}" for w in ("R12", "N3", "EXT", "W0", "W00") if r[w])
            print(f" {v} {line} mdd(N3){r['N3']['mdd'] if r['N3'] else None} trim(R12/N3){[r[w]['trim_both5'] for w in ('R12','N3') if r[w]]} med{[r[w]['median'] for w in ('R12','N3') if r[w]]}")
            if "criteria" in r:
                print("   ", "PASS" if r["pass"] else "FAIL", "".join("✓" if x else "✗" for x in r["criteria"].values()), r["criteria"])
            print("    overlap", r["overlap"]["N3_share_by_skill"], "any", r["overlap"]["N3_share_any"], "L", r["bought"]["L_dist_N3"], "hold", r["N3"]["hold"] if r["N3"] else None)


if __name__ == "__main__":
    main()
