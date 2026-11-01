"""§275 评估：5 条预注册标准。"""
import json, sys
res = json.load(open(sys.argv[1]))
P8 = ["P19 2019", "P20 2020", "P21 2021", "W0 2022-04~2023-03", "W00 2023-04~12", "W1 2024", "W2 2025-01~09", "R12 2025-10~2026-10"]
N3, R12, W00 = "N3 2024-01~2026-10", "R12 2025-10~2026-10", "W00 2023-04~12"
for n, v in res.items():
    ex = [v[p].get("excess", -99) for p in P8]; av = [v[p].get("avg", -99) for p in P8]
    ok = [v[R12]["avg"] > 0 and v[N3]["avg"] > 0 and v[R12]["pf"] >= 1.3 and v[N3]["pf"] >= 1.3,
          v[R12]["n"] >= 20 and v[N3]["n"] >= 50,
          sum(x > 0 for x in ex) >= 6 and v[R12]["excess"] > 0 and v[W00]["excess"] > 0 and v[N3]["excess"] >= 0.5,
          sum(x > 0 for x in av) >= 5,
          v[N3]["mdd"] >= -15 and v[N3]["streak"] <= 12]
    print(f"{n}: R12 n={v[R12]['n']:.0f} avg={v[R12]['avg']:+.2f} pf={v[R12]['pf']} ex={v[R12]['excess']:+.2f} | N3 n={v[N3]['n']:.0f} avg={v[N3]['avg']:+.2f} pf={v[N3]['pf']} ex={v[N3]['excess']:+.2f} mdd={v[N3]['mdd']} streak={v[N3]['streak']:.0f}"
          f" | 超额>0 {sum(x>0 for x in ex)}/8 均值>0 {sum(x>0 for x in av)}/8 | {''.join('✓' if x else '✗' for x in ok)} → {'采纳' if all(ok) else '否决'}")
    print("  各段每笔", " ".join(f"{x:+.2f}" for x in av), "| 超额", " ".join(f"{x:+.2f}" for x in ex))
