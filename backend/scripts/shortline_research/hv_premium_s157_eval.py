"""§157 评估：5 条预注册标准。"""
import json, sys
from statistics import median
res = json.load(open(sys.argv[1]))
P8 = ["P19 2019", "P20 2020", "P21 2021", "W0 2022-04~2023-03", "W00 2023-04~12", "W1 2024", "W2 2025-01~09", "R12 2025-10~2026-10"]
N3, R12, W00 = "N3 2024-01~2026-10", P8[-1], P8[4]
ctrl = res["CTRL"]
for s, v in res.items():
    av = [v[p].get("avg", -99) for p in P8]; ns = [v[p].get("n", 0) for p in P8]
    beat = sum(v[p].get("avg", -99) > ctrl[p].get("avg", -99) for p in P8)
    ok = [sum(x > 0 for x in av) >= 6, v[R12].get("avg", -99) > 0 and v[W00].get("avg", -99) > 0, v[N3].get("pf", 0) >= 1.3, beat >= 5, median(ns) >= 30]
    print(f"{s}: 每笔 " + " ".join(f"{x:+.2f}" for x in av) + f" | n中位 {median(ns):.0f} 胜对照 {beat}/8 | N3 n={v[N3]['n']} avg={v[N3]['avg']:+.2f} pf={v[N3]['pf']} win={v[N3]['win']} mdd={v[N3]['mdd']} 连亏{v[N3]['max_loss_streak']}"
          f" | R12 n={v[R12]['n']} avg={v[R12]['avg']:+.2f} pf={v[R12]['pf']} mdd={v[R12]['mdd']} | {''.join('✓' if x else '✗' for x in ok)} → {'采纳' if all(ok) and s != 'CTRL' else '否'}")
