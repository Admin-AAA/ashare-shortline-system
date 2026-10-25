"""§153 评估：按预注册 5 条标准逐策略判定。"""
import json
import sys

res = json.load(open(sys.argv[1]))
P8 = ["P19 2019", "P20 2020", "P21 2021", "W0 2022-04~2023-03", "W00 2023-04~12", "W1 2024", "W2 2025-01~09", "R12 2025-10~2026-10"]
N3, R12 = "N3 2024-01~2026-10", "R12 2025-10~2026-10"
for s, vs in res.items():
    b = vs["base"]
    print(f"\n{s} base: " + " ".join(f"{b[p]['avg']:+.2f}" for p in P8 if p in b)
          + f" | N3 n={b[N3]['n']:.0f} avg={b[N3]['avg']:+.2f} pf={b[N3]['pf']} mdd={b[N3]['mdd']} tot={b[N3]['tot']}")
    for g, v in vs.items():
        if g == "base" or N3 not in v:
            continue
        wins = sum(1 for p in P8 if p in v and v[p].get("avg", -99) >= b[p].get("avg", -99))
        ok = [v[N3]["avg"] >= b[N3]["avg"], v[R12]["avg"] >= b[R12]["avg"], wins >= 5,
              v[N3]["pf"] >= b[N3]["pf"], v[N3]["mdd"] >= b[N3]["mdd"]]
        print(f"  {g}: " + " ".join(f"{v[p]['avg']:+.2f}" for p in P8 if p in v)
              + f" | N3 n={v[N3]['n']:.0f} avg={v[N3]['avg']:+.2f} pf={v[N3]['pf']} mdd={v[N3]['mdd']} tot={v[N3]['tot']}"
              + f" | 胜段 {wins}/8 标准 {''.join('✓' if x else '✗' for x in ok)} → {'采纳' if all(ok) else '否'}")
