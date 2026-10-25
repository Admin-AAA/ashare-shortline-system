"""§154 评估：A/B 新策略按 §91 加严 5 条；C 过滤按 §153 5 条（对比无过滤基线）。"""
import json
import sys
from statistics import median

res = json.load(open(sys.argv[1]))
P8 = ["P19 2019", "P20 2020", "P21 2021", "W0 2022-04~2023-03", "W00 2023-04~12", "W1 2024", "W2 2025-01~09", "R12 2025-10~2026-10"]
N3, R12, W00 = "N3 2024-01~2026-10", "R12 2025-10~2026-10", "W00 2023-04~12"
mk = lambda ok: "".join("✓" if x else "✗" for x in ok)

print("== A/B 新策略 ==")
for s, v in res.items():
    if s.startswith("C_"):
        continue
    ex = [v.get(p, {}).get("excess", -99) for p in P8]
    av = [v.get(p, {}).get("avg", -99) for p in P8]
    ns = [v.get(p, {}).get("n", 0) for p in P8]
    ok = [sum(x > 0 for x in ex) >= 6, v[R12].get("excess", -99) > 0 and v[W00].get("excess", -99) > 0,
          sum(x > 0 for x in av) >= 5, median(ns) >= 15, v[N3].get("pf", 0) >= 1.3]
    print(f"{s}: 超额 " + " ".join(f"{x:+.2f}" for x in ex) + f" | 每笔 " + " ".join(f"{x:+.2f}" for x in av)
          + f" | n中位 {median(ns):.0f} | N3 n={v[N3].get('n', 0):.0f} avg={v[N3].get('avg', 0):+.2f} pf={v[N3].get('pf', 0)}"
          f" mdd={v[N3].get('mdd', 0)} | {mk(ok)} → {'采纳' if all(ok) else '否'}")

print("\n== C 减持回避过滤 ==")
for s, vs in res.items():
    if not s.startswith("C_"):
        continue
    b, v = vs["base"], vs["red"]
    wins = sum(1 for p in P8 if v[p].get("avg", -99) >= b[p].get("avg", -99))
    ok = [v[N3]["avg"] >= b[N3]["avg"], v[R12]["avg"] >= b[R12]["avg"], wins >= 5, v[N3]["pf"] >= b[N3]["pf"], v[N3]["mdd"] >= b[N3]["mdd"]]
    print(f"{s[2:]}: 基线 " + " ".join(f"{b[p]['avg']:+.2f}" for p in P8) + f" | N3 n={b[N3]['n']:.0f} avg={b[N3]['avg']:+.2f} pf={b[N3]['pf']} mdd={b[N3]['mdd']}")
    print(f"   过滤 " + " ".join(f"{v[p]['avg']:+.2f}" for p in P8) + f" | N3 n={v[N3]['n']:.0f} avg={v[N3]['avg']:+.2f} pf={v[N3]['pf']} mdd={v[N3]['mdd']}"
          f" | 胜段 {wins}/8 {mk(ok)} → {'采纳' if all(ok) else '否'}")
