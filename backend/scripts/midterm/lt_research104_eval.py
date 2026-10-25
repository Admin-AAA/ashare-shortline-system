"""§277 评估：预注册 7 条（5 条族标准 + R12/N3 每笔>0、R12 PF≥1.3、N3 MDD≥−35 + 与 SCW 重叠≥50% 时须 N3 每笔≥SCW）。"""
import json, sys
from statistics import median
res = json.load(open(sys.argv[1]))
P8 = ["P19 2019", "P20 2020", "P21 2021", "W0 2022-04~2023-03", "W00 2023-04~12", "W1 2024", "W2 2025-01~09", "R12 2025-10~2026-10"]
N3 = "N3 2024-01~2026-10"
R12 = "R12 2025-10~2026-10"
info = res.get('_info', {})
scw = res.get('SCW', {})
for s, v in res.items():
    if s.startswith('_'): continue
    ex = [v[p].get("excess", -99) for p in P8]
    av = [v[p].get("avg", -99) for p in P8]
    ns = [v[p].get("n", 0) for p in P8]
    ok = [
        sum(x > 0 for x in ex) >= 6,
        v["R12 2025-10~2026-10"].get("excess", -99) > 0 and v["W00 2023-04~12"].get("excess", -99) > 0,
        sum(x > 0 for x in av) >= 5,
        median(ns) >= 15,
        v[N3].get("pf", 0) >= 1.3,
        v[R12].get("avg", -99) > 0 and v[N3].get("avg", -99) > 0 and v[R12].get("pf", 0) >= 1.3 and v[N3].get("mdd", -99) >= -35,
    ]
    if s in ("LPS_M", "LPS_W"):
        ovs = [info.get(p, {}).get(f"{s}_overlap") for p in P8 + [N3]]
        ovs = [x for x in ovs if x is not None]
        ov = sum(ovs) / len(ovs) if ovs else 0
        ok.append(ov < 0.5 or v[N3].get("avg", -99) >= scw.get(N3, {}).get("avg", 99))
        print(f"  {s} 平均重叠 SCW {ov:.2f}")
    print(f"{s}: 超额 " + " ".join(f"{x:+.2f}" for x in ex)
          + " | 每笔 " + " ".join(f"{x:+.2f}" for x in av)
          + f" | n中位 {median(ns):.0f} | N3 n={v[N3].get('n')} avg={v[N3].get('avg',0):+.2f} pf={v[N3].get('pf')} mdd={v[N3].get('mdd')} ex={v[N3].get('excess',0):+.2f}"
          + f" | R12 n={v['R12 2025-10~2026-10'].get('n')} avg={v['R12 2025-10~2026-10'].get('avg',0):+.2f} pf={v['R12 2025-10~2026-10'].get('pf')} ex={v['R12 2025-10~2026-10'].get('excess',0):+.2f}"
          + f" | {''.join('✓' if x else '✗' for x in ok)} → {'采纳' if (all(ok) and s != 'SCW') else ('参照' if s == 'SCW' else '否')}")
