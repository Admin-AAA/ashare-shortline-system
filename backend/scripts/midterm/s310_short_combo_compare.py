"""§310：短线「4技能合并去重(shortline-combo)」vs 4 个单技能。只读：§282 保存的 BASE 逐笔 + index_daily。预注册见 docs §310。
用法：cd backend && .venv/bin/python scripts/midterm/s310_short_combo_compare.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND)); sys.path.insert(0, str(BACKEND / "scripts/midterm"))
from s282_quality import tail_metrics  # noqa: E402
from s309_excess_hs300 import SHORT_SKILLS, SHORT_TRADES, idx_returns, load_hs300, summarize  # noqa: E402

OUT = BACKEND / "output/research/s310/compare.json"
PRIO = {s: i for i, s in enumerate(SHORT_SKILLS)}
WIN = {"R12": ["12m"], "N3": ["12m", "EXT"], "EXT": ["EXT"], "W0": ["W0"], "W00": ["W00"]}


def dedup(trades: list[dict]) -> tuple[list[dict], dict]:
    """按 (信号日, 代码) 去重，优先级固定；返回保留笔 + 重复统计。"""
    groups: dict = {}
    for t in trades:
        groups.setdefault((t["dt"], t["code"]), []).append(t)
    kept, dup_groups, dup_trades, diff = [], 0, 0, 0.0
    for k, g in groups.items():
        g = sorted(g, key=lambda t: PRIO[t["sid"]])
        kept.append(g[0])
        if len(g) > 1:
            dup_groups += 1
            dup_trades += len(g)
            diff = max(diff, max(abs(x["net"] - g[0]["net"]) for x in g))
    return kept, {"n_before": len(trades), "n_after": len(kept), "dup_keys": dup_groups, "dup_trades_in_dup_keys": dup_trades,
                  "dup_share": round(dup_groups / len(groups), 4), "max_net_diff_pp": round(diff, 6),
                  "diff_entry_exit": sum(1 for g in groups.values() if len(g) > 1 and len({(x['en'], x['ex']) for x in g}) > 1)}


def metrics(ts: list[dict]) -> dict:
    df = pd.DataFrame(ts)
    net = df["net"]
    wins, losses = net[net > 0], net[net < 0]
    daily = df.groupby("dt")["net"].mean().sort_index()
    cum = daily.cumsum()
    seq = [r["net"] for r in sorted(ts, key=lambda r: (r["ex"], r["dt"], r["code"]))]
    tm = tail_metrics(seq)
    streak = best = 0
    for x in seq:
        streak = streak + 1 if x < 0 else 0
        best = max(best, streak)
    return {"n": len(df), "avg": round(float(net.mean()), 3), "pf": round(float(wins.sum() / max(abs(losses.sum()), 1e-9)), 3),
            "mdd": round(float((cum - cum.cummax()).min()), 2), "streak": best, "median": round(tm["median"], 3),
            "trim_both5": round(tm["trim_both5"], 3) if tm["trim_both5"] is not None else None,
            "top10_share": round(tm["top10_share"], 3) if tm["top10_share"] is not None else None,
            "picks_per_day": round(len(df) / df["dt"].nunique(), 2), "signal_days": int(df["dt"].nunique())}


def concurrency(ts: list[dict], cal: list[str]) -> dict:
    cnt = {d: 0 for d in cal}
    for t in ts:
        e, x = f"{t['en'][:4]}-{t['en'][4:6]}-{t['en'][6:]}", f"{t['ex'][:4]}-{t['ex'][4:6]}-{t['ex'][6:]}"
        for d in cal:
            if e <= d <= x:
                cnt[d] += 1
    v = [c for c in cnt.values() if c > 0]
    return {"avg_open": round(sum(v) / len(v), 2) if v else 0, "max_open": max(v) if v else 0}


def wavg(rows: list[dict], key: str) -> float:
    tot = sum(r["n"] for r in rows)
    return round(sum(r[key] * r["n"] for r in rows) / tot, 3)


def main() -> None:
    st = json.loads(SHORT_TRADES.read_text())
    ix = load_hs300()
    res: dict = {}
    for w, parts in WIN.items():
        allt = [t for p in parts for t in st[p]]
        singles = {s: metrics([t for t in allt if t["sid"] == s]) for s in SHORT_SKILLS}
        kept, dstat = dedup(allt)
        combo = metrics(kept)
        combo_raw = metrics(allt)  # 不去重（= 页面现有「4技能合并」行）
        r = {"singles": singles, "combo": combo, "combo_nodedup": combo_raw, "dedup": dstat,
             "wavg": {k: wavg(list(singles.values()), k) for k in ("avg", "pf", "mdd")}}
        if w in ("R12", "N3"):
            cal = [d for d in ix.index if d >= ("2025-10-01" if w == "R12" else "2024-01-01")]
            r["concurrency"] = {"combo": concurrency(kept, cal), **{s: concurrency([t for t in allt if t["sid"] == s], cal) for s in SHORT_SKILLS}}
            for name, ts in (("combo", kept), *[(s, [t for t in allt if t["sid"] == s]) for s in SHORT_SKILLS]):
                e = summarize([t["net"] for t in ts], idx_returns(ix, [t["en"] for t in ts], [t["ex"] for t in ts], "close"))
                (combo if name == "combo" else singles[name]).update(e)
        res[w] = r
    # 阶段：EXT / 12m / W0 / W00 （12m 用 R12）
    ph = {"combo": {}, **{s: {} for s in SHORT_SKILLS}}
    for w in ("EXT", "R12", "W0", "W00"):
        ph["combo"][w] = res[w]["combo"]["avg"]
        for s in SHORT_SKILLS:
            ph[s][w] = res[w]["singles"][s]["avg"]
    res["phases"] = {k: {"avg_by_window": v, "positive": sum(1 for x in v.values() if x > 0)} for k, v in ph.items()}
    # 判定
    c = {}
    for w in ("R12", "N3"):
        r = res[w]
        c[f"1_avg_pos_{w}"] = r["combo"]["avg"] > 0
        c[f"2_avg_ge_wavg_{w}"] = r["combo"]["avg"] >= r["wavg"]["avg"]
        c[f"2_pf_ge_wavg_{w}"] = r["combo"]["pf"] >= r["wavg"]["pf"]
        c[f"3_mdd_not_worse_{w}"] = r["combo"]["mdd"] >= r["wavg"]["mdd"]  # mdd 为负数，越大(越接近0)越好
        c[f"4_pf_ge_1.3_{w}"] = r["combo"]["pf"] >= 1.3
        c[f"4_mdd_le_35_{w}"] = r["combo"]["mdd"] >= -35
        c[f"5_n_ge_30_{w}"] = r["combo"]["n"] >= 30
    c["4_phases_ge_3"] = res["phases"]["combo"]["positive"] >= 3
    c["4_bear_gt_-5"] = res["W0"]["combo"]["avg"] > -5 and res["W00"]["combo"]["avg"] > -5
    res["criteria"] = c
    res["all_pass"] = all(c.values())
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float))
    print(json.dumps(res, ensure_ascii=False, indent=1, default=float))


if __name__ == "__main__":
    main()
