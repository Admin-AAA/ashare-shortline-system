"""§274 评估：按预注册 5 条标准。"""
import json, sys
import numpy as np
res = json.load(open(sys.argv[1]))
P8 = ["P19 2019", "P20 2020", "P21 2021", "W0 2022-04~2023-03", "W00 2023-04~12", "W1 2024", "W2 2025-01~09", "R12 2025-10~2026-10"]
N3, R12, W00 = "N3 2024-01~2026-10", "R12 2025-10~2026-10", "W00 2023-04~12"
for n in ("B1", "B2", "B3", "B1_20", "B2_20", "B3_20"):
    v = res[n]; ct = "CTL_20" if n.endswith("_20") else "CTL_M"
    C = {p: [res[f"{ct}_{s}"][p] for s in range(10)] for p in (R12, N3)}
    cm = {p: np.mean([x.get("avg", 0) for x in C[p]]) for p in C}
    n3b = sum(v[N3].get("avg", -9) > x.get("avg", 0) for x in C[N3])
    ok = [v[R12].get("avg", -9) > 0 and v[N3].get("avg", -9) > 0 and v[R12].get("pf", 0) >= 1.3 and v[N3].get("pf", 0) >= 1.3,
          v[R12].get("n", 0) >= 30 and v[N3].get("n", 0) >= 60,
          sum(v[p].get("avg", -9) > 0 for p in P8) >= 5 and v[W00].get("avg", -9) > 0 and v[R12].get("avg", -9) > 0,
          v[R12].get("avg", -9) - cm[R12] >= 0.3 and v[N3].get("avg", -9) - cm[N3] >= 0.3 and n3b >= 8,
          v[N3].get("mdd", -99) >= -15 and v[N3].get("streak", 99) <= 12]
    print(f"{n}: R12 n={v[R12]['n']:.0f} avg={v[R12]['avg']:+.2f} pf={v[R12]['pf']} | N3 n={v[N3]['n']:.0f} win={v[N3]['win']} avg={v[N3]['avg']:+.2f} pf={v[N3]['pf']} mdd={v[N3]['mdd']} streak={v[N3].get('streak')}"
          f" | 对照均值 R12 {cm[R12]:+.2f} N3 {cm[N3]:+.2f} (N3 胜 {n3b}/10 套) | 均值>0 {sum(v[p].get('avg',-9)>0 for p in P8)}/8 段 | {''.join('✓' if x else '✗' for x in ok)} → {'采纳' if all(ok) else '否决'}")
