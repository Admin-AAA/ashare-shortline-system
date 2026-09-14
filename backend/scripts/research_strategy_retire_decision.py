"""策略退役决策表（只读）：把"完全负收益"拆成三层可复现证据再判定。

三层证据（缺一层都不足以删策略，见 L-110/L-111(d)）：
1. path-A solo（探索层，output/research/per_strategy/{frozen,forward,run3}.json）——只能排序；
2. canonical 三窗"选中卡背书"（生产同构选择 + 真实出场，ablate_fast_W{1,2,3}.json 的 provenance）；
3. 生产同构 A/B 消融（剔除该 skill 后 Δcohort 的配对 bootstrap CI + 逐月同号，同文件 arms）。
另外附 LIVE 已结算分桶（output/champion_baseline_*.json bucket_breakdown）作实盘参照。

判定规则（写死，避免事后解释）：
- DELETE 候选 = 三窗"零贡献/不伤组合"（每个窗口 Δ≥0 且独持卡非正收益）
  且（solo 全窗负 或 从未进入任何选中卡）。
- KEEP = 任一窗口剔除后组合变差（Δ<0）。

用法：cd backend && .venv/bin/python scripts/research_strategy_retire_decision.py
"""
from __future__ import annotations

import json
from pathlib import Path

B = Path(__file__).resolve().parents[1]
OUT = B / "output" / "research" / "per_strategy"
WINDOWS = ("W1", "W2", "W3")


def _load(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text())


def main() -> None:
    ablate = {}
    for w in WINDOWS:
        data = _load(OUT / f"ablate_fast_{w}.json")
        if data:
            ablate[w] = data.get(w) or next(iter(data.values()))
    solo = {w: _load(OUT / f"{name}.json") for w, name in
            zip(WINDOWS, ("frozen", "forward", "run3"))}
    live = _load(B.parent / "output" / "champion_baseline_20260913.json") or {}
    live_buckets: dict[str, dict] = {}
    for pop in live.get("populations") or []:
        if str(pop.get("tag", "")).startswith("FORMAL_ALL_LEGACY/all_scores"):
            live_buckets = pop.get("bucket_breakdown") or {}

    for w in WINDOWS:
        if w not in ablate:
            print(f"!! 缺 {w} 的 ablate_fast 结果，先跑 research_strategy_ablation_fast.py")
            return
    skills = sorted(ablate["W1"]["arms"])
    solo_by_skill: dict[str, list] = {s: [] for s in skills}
    for s in skills:
        for w in WINDOWS:
            rec = None
            for row in solo[w] or []:
                if row.get("strategy_id") == s:
                    rec = row
            solo_by_skill[s].append(rec)

    print("=== 三层证据（全管线重放 / production_selection v4 + execution v4） ===")
    print(f"{'skill':34s} {'solo W1/W2/W3':>24s}  {'Δcohort W1/W2/W3':>26s}  {'独持卡 W1/W2/W3':>22s}")
    rows = []
    for s in skills:
        dlt, own = [], []
        for w in WINDOWS:
            arm = ablate[w]["arms"].get(s) or {}
            dlt.append(arm.get("delta_cohort"))
            own.append((arm.get("only_A_cards"), arm.get("only_A_avg")))
        solo_txt = "/".join(
            "n=0" if (r is None or not r.get("n")) else f"{r['avg_net']:+.3f}" for r in solo_by_skill[s]
        )
        d_txt = "/".join("n/a" if d is None else f"{d:+.4f}" for d in dlt)
        own_txt = "/".join(f"{n}张{'--' if a is None else format(a, '+.2f')}" for n, a in own)
        rows.append({"skill": s, "solo": solo_by_skill[s], "delta": dlt, "own": own,
                     "live": live_buckets.get(s)})
        print(f"{s:34s} {solo_txt:>24s}  {d_txt:>26s}  {own_txt:>22s}")

    print("\n=== LIVE 实体分桶（FORMAL_ALL_LEGACY/all_scores） ===")
    for r in sorted(rows, key=lambda x: (x["live"] or {}).get("avg_net_pct", 0)):
        lb = r["live"]
        if not lb:
            print(f"{r['skill']:34s} 无 LIVE 分桶（LIVE 期从未成为选中卡主导策略）")
            continue
        print(f"{r['skill']:34s} n={lb['n']:>4d} WR={lb['win_rate_pct']:>5.1f}% "
              f"avg={lb['avg_net_pct']:+7.3f}% PF={lb['profit_factor']} max_loss={lb['max_loss_pct']}")

    print("\n=== 判定 ===")
    for r in rows:
        zero_contrib = all(
            (d is None or d >= -1e-9) and (n == 0 or (a is not None and a <= 0))
            for d, (n, a) in zip(r["delta"], r["own"])
        )
        hurts = any((d is not None and d < -1e-9) for d in r["delta"])
        neg_solo = all((s is None or not s.get("n")) or s.get("avg_net", 0) < 0 for s in r["solo"])
        never_selected = all((s is None or not s.get("n")) for s in r["solo"]) and all(n == 0 for _d, (n, _a) in zip(r["delta"], r["own"]))
        if zero_contrib and (neg_solo or never_selected):
            verdict = "DELETE候选"
        elif hurts:
            verdict = "KEEP(剔除变差)"
        else:
            verdict = "KEEP(证据不足)"
        print(f"{r['skill']:34s} {verdict}")


if __name__ == "__main__":
    main()
