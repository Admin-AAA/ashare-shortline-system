"""§188 评估：5 条预注册标准（同 §186）。"""
import json, sys
from statistics import median
res = json.load(open(sys.argv[1]))
P8 = ["P19 2019", "P20 2020", "P21 2021", "W0 2022-04~2023-03", "W00 2023-04~12", "W1 2024", "W2 2025-01~09", "R12 2025-10~2026-10"]
N3, R12, W00 = "N3 2024-01~2026-10", P8[-1], P8[4]
mk = lambda ok: "".join("✓" if x else "✗" for x in ok)
for s, v in res.items():
    ex = [v.get(p, {}).get("excess", -99) for p in P8]
    av = [v.get(p, {}).get("avg", -99) for p in P8]
    ns = [v.get(p, {}).get("n", 0) for p in P8]
    ok = [sum(x > 0 for x in ex) >= 6, v.get(R12, {}).get("excess", -99) > 0 and v.get(W00, {}).get("excess", -99) > 0,
          sum(x > 0 for x in av) >= 5, median(ns) >= 15, v.get(N3, {}).get("pf", 0) >= 1.3]
    print(f"{s}: 超额 " + " ".join(f"{x:+.2f}" for x in ex) + " | 每笔 " + " ".join(f"{x:+.2f}" for x in av)
          + f" | n中位 {median(ns):.0f} | N3 n={v.get(N3,{}).get('n',0):.0f} avg={v.get(N3,{}).get('avg',0):+.2f} pf={v.get(N3,{}).get('pf',0)}"
          f" mdd={v.get(N3,{}).get('mdd',0)} | R12 n={v.get(R12,{}).get('n',0):.0f} avg={v.get(R12,{}).get('avg',0):+.2f} pf={v.get(R12,{}).get('pf',0)} ex={v.get(R12,{}).get('excess',0):+.2f}"
          f" | {mk(ok)} → {'采纳' if all(ok) else '否'}")
