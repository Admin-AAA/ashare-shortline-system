"""§281 评估：预注册 6 条（§271 族 5 条 + 第 6 条）。"""
import json, sys
from statistics import median
res = json.load(open(sys.argv[1]))
P8 = ["P19 2019", "P20 2020", "P21 2021", "W0 2022-04~2023-03", "W00 2023-04~12", "W1 2024", "W2 2025-01~09", "R12 2025-10~2026-10"]
R12, N3 = "R12 2025-10~2026-10", "N3 2024-01~2026-10"
for s, v in res.items():
    ex = [v[p].get("excess", -99) for p in P8]; av = [v[p].get("avg", -99) for p in P8]; ns = [v[p].get("n", 0) for p in P8]
    r, q = v[R12], v[N3]
    ok = [sum(x > 0 for x in ex) >= 6,
          r.get("excess", -99) > 0 and v["W00 2023-04~12"].get("excess", -99) > 0,
          sum(x > 0 for x in av) >= 5,
          median(ns) >= 15,
          q.get("pf", 0) >= 1.3,
          r.get("avg", -99) > 0 and q.get("avg", -99) > 0 and r.get("pf", 0) >= 1.3 and q.get("mdd", -99) >= -35]
    print(f"{s}: 超额 " + " ".join(f"{x:+.2f}" for x in ex) + " | 每笔 " + " ".join(f"{x:+.2f}" for x in av)
          + f" | n " + " ".join(str(int(x)) for x in ns)
          + f" | R12 n={r.get('n')} avg={r.get('avg',0):+.2f} pf={r.get('pf')} mdd={r.get('mdd')} ex={r.get('excess',0):+.2f} streak={r.get('streak')}"
          + f" | N3 n={q.get('n')} avg={q.get('avg',0):+.2f} pf={q.get('pf')} mdd={q.get('mdd')} ex={q.get('excess',0):+.2f} streak={q.get('streak')} p05={q.get('p05')}"
          + f" | {''.join('✓' if x else '✗' for x in ok)} → {'采纳' if all(ok) else '否'}")
