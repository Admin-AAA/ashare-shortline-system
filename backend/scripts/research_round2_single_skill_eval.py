"""round2 单策略近12月评估：读 panels，输出 total / 季度 / post-10-10 settled 切片。

口径：only:<skill> + production_selection v27 + output_count=6 + score_line=72；
净收益用面板已写入的 net_by_code（execution policy v5.0.0）。
组合基线取 stage5 六策略面板同窗（避免 round2 研究技能污染 A 臂）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from app.engine.production_policy import load_production_selection_policy
import scripts.research_entry_rule_panel as mod
from scripts.research_entry_rule_panel import RULES, _metrics, _boot_ci, run_rule

RESEARCH = [
    "oversold-capital-reversal",
    "low-position-first-board",
    "second-board-leader",
    "sector-laggard-catchup",
    "within-industry-reversal",
    "low-max-steady-trend",
    "northbound-accumulation",
    "institutional-survey-attention",
    "dragon-tiger-net-buy-followup",
]
PRODUCTION = [
    "chao-gu-yang-jia-skill",
    "chen-xiaoqun-skill",
    "chip-single-peak-breakout",
    "high-range-breakout-momentum",
    "limit-up-reseal-relay",
    "sector-relative-strength-leader",
]
QUARTERS = [
    ("25Q4", "2025-10-01", "2026-01-01"),
    ("26Q1", "2026-01-01", "2026-04-01"),
    ("26Q2", "2026-04-01", "2026-07-01"),
    ("26Q3", "2026-07-01", "2026-10-10"),
]
EVAL0, EVAL1 = "2025-10-01", "2026-10-10"
POST0 = "2026-10-10"


def _slice_panel(panel: dict, start: str, end_exclusive: str) -> dict:
    days = [d for d in panel["days"] if start <= d["signal_date"] < end_exclusive]
    return {**panel, "days": days, "start": start, "end": end_exclusive}


def _register_only(panel: dict) -> list[str]:
    skills = sorted({
        str(s.get("strategy_id"))
        for d in panel["days"]
        for c in d["cards"]
        for s in (c.get("strategy_signals") or [])
        if isinstance(s, dict) and s.get("strategy_id")
    })
    for sid in skills:
        RULES[f"only:{sid}"] = (
            lambda cards, _d, _sid=sid: [
                c for c in cards
                if any(
                    str(s.get("strategy_id")) == _sid and not s.get("hard_veto")
                    for s in (c.get("strategy_signals") or []) if isinstance(s, dict)
                )
            ]
        )
    return skills


def _with_quarters(rows: list[dict], all_days: list[str]) -> dict | None:
    m = _metrics(rows, all_days)
    if m is None:
        return None
    q = {}
    for name, lo, hi in QUARTERS:
        sub = [r for r in rows if lo <= r["dt"] < hi]
        qm = _metrics(sub, [d for d in all_days if lo <= d < hi]) if sub else None
        q[name] = None if qm is None else {k: qm[k] for k in ("n", "wr", "avg", "pf", "sum", "mdd")}
    m["quarters"] = q
    return m


def _eval_rule(rule, panel: dict, pol, output_count: int, score_line: float):
    old_oc, old_sl = mod.OUTPUT_COUNT, mod.SCORE_LINE
    mod.OUTPUT_COUNT = output_count
    mod.SCORE_LINE = score_line
    try:
        _m, rows = run_rule(rule, panel, pol, apply_global_weights=False)
    finally:
        mod.OUTPUT_COUNT = old_oc
        mod.SCORE_LINE = old_sl
    all_days = [d["signal_date"] for d in panel["days"]]
    full = _with_quarters(rows, all_days)
    post_rows = [r for r in rows if r["dt"] >= POST0]
    post_days = [d for d in all_days if d >= POST0]
    post = _metrics(post_rows, post_days) if post_rows else None
    if full is not None:
        full["post_1010"] = None if post is None else {k: post[k] for k in ("n", "wr", "avg", "pf", "sum", "mdd")}
        full.pop("daily", None)
    return full, rows


def _daily(rs, days):
    if not rs:
        return pd.Series(0.0, index=days)
    df = pd.DataFrame(rs)
    return df.groupby("dt").net.mean().reindex(days).fillna(0.0)


def main() -> None:
    out_path = Path("output/research/round2/single_skill.json")
    research_panel = json.loads(Path("output/research/round2/panel/entry_panel_WX.json").read_text())
    prod_panel = json.loads(Path("output/research/stage5/panel6/entry_panel_WX.json").read_text())
    rp = _slice_panel(research_panel, EVAL0, EVAL1)
    pp = _slice_panel(prod_panel, EVAL0, EVAL1)
    pol = load_production_selection_policy()
    oc, sl = 6, 72.0
    _register_only(research_panel)
    _register_only(prod_panel)

    results = {
        "window": f"{EVAL0}~{EVAL1} (exclusive end ≈ thru 2026-10-09)",
        "policy": f"production_selection.v{pol.version}",
        "execution_note": research_panel.get("policy_note"),
        "output_count": oc,
        "score_line": sl,
    }

    base_m, base_rows = _eval_rule(RULES["identity"], pp, pol, oc, sl)
    results["production_combined_baseline"] = base_m
    print("=== production combined (stage5 last12m) ===",
          {k: base_m[k] for k in ("n", "wr", "avg", "pf", "sum", "mdd")})
    print("quarters avg", {k: (v["avg"] if v else None) for k, v in base_m["quarters"].items()})

    poll_m, _ = _eval_rule(RULES["identity"], rp, pol, oc, sl)
    results["round2_identity_polluted"] = poll_m
    print("=== round2 identity polluted ===",
          {k: poll_m[k] for k in ("n", "wr", "avg", "pf", "sum", "mdd")} if poll_m else None)

    per = {}
    for sid in PRODUCTION:
        m, rows = _eval_rule(RULES[f"only:{sid}"], pp, pol, oc, sl)
        per[sid] = {"group": "production", **(m or {"n": 0})}
        print(f"[prod {sid}] n={per[sid].get('n')} wr={per[sid].get('wr')} avg={per[sid].get('avg')} pf={per[sid].get('pf')}")

    for sid in RESEARCH:
        rule = RULES.get(f"only:{sid}")
        if rule is None:
            per[sid] = {"group": "research", "n": 0, "qualify": False, "qualify_reason": "not in panel"}
            print(f"[research {sid}] MISSING")
            continue
        m, rows = _eval_rule(rule, rp, pol, oc, sl)
        if m and base_m and rows is not None:
            common = sorted({d["signal_date"] for d in pp["days"]} & {d["signal_date"] for d in rp["days"]})
            dlt = (_daily(rows, common) - _daily(base_rows, common)).values
            md, lo, hi = _boot_ci(dlt)
            m["delta_cohort_vs_prod_combined"] = {"mean": md, "ci95": [lo, hi]}
        entry = {"group": "research", **(m or {"n": 0})}
        if not entry.get("n"):
            reason = "无成交"
            ok = False
        else:
            base_avg, base_wr = float(base_m["avg"]), float(base_m["wr"])
            ok_avg = entry["avg"] >= base_avg
            ok_wr = entry["wr"] >= base_wr
            ok_pf = entry["pf"] > 1.3
            ok_n = entry["n"] >= 40
            post = entry.get("post_1010")
            ok_post = not (post and post["n"] >= 3 and post["avg"] < -1.0)
            parts = []
            if not ok_avg: parts.append(f"avg {entry['avg']:.3f} < base {base_avg:.3f}")
            if not ok_wr: parts.append(f"wr {entry['wr']*100:.1f}% < base {base_wr*100:.1f}%")
            if not ok_pf: parts.append(f"pf {entry['pf']:.3f} <= 1.3")
            if not ok_n: parts.append(f"n {entry['n']} < 40")
            if not ok_post: parts.append(f"post-10-10 avg {post['avg']:.3f} 明显失败")
            ok = ok_avg and ok_wr and ok_pf and ok_n and ok_post
            reason = "QUALIFY" if ok else "; ".join(parts)
        entry["qualify"] = ok
        entry["qualify_reason"] = reason
        per[sid] = entry
        print(f"[research {sid}] n={entry.get('n')} wr={entry.get('wr')} avg={entry.get('avg')} pf={entry.get('pf')} → {reason}")

    results["skills"] = per
    results["qualifies"] = [s for s in RESEARCH if per.get(s, {}).get("qualify")]
    results["baseline_note"] = (
        "生产组合基线 = stage5/panel6 切片；round2 A/identity 因研究技能进池被污染，不作基线。"
    )
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    print("[saved]", out_path)
    print("qualifies:", results["qualifies"])


if __name__ == "__main__":
    main()
