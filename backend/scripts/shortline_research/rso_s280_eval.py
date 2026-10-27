"""§280 评估：按预注册 6 条判 RSO_1 / RSO_2。"""
import json, sys
res = json.load(open(sys.argv[1]))
P5 = ["W0 2022-04~2023-03", "W00 2023-04~12", "W1 2024", "W2 2025-01~09", "R12 2025-10~2026-10"]
R12, N3 = "R12 2025-10~2026-10", "N3 2024-01~2026-10"
ctl = {p: [res[f"CTL_{s}"][p].get("avg", -99) for s in range(10)] for p in P5 + [N3]}
cm = {p: sum(v) / len(v) for p, v in ctl.items()}
print("CTL 均值: " + " ".join(f"{p.split()[0]} {cm[p]:+.2f}" for p in P5 + [N3]))
for n in ("RSO_1", "RSO_2"):
    v = res[n]; r, q = v[R12], v[N3]
    av = [v[p].get("avg", -99) for p in P5]
    ok = [
        r.get("avg", -99) > 0 and q.get("avg", -99) > 0 and r.get("pf", 0) >= 1.3 and q.get("pf", 0) >= 1.3,
        r.get("n", 0) >= 30 and q.get("n", 0) >= 60,
        sum(x > 0 for x in av) >= 4 and v["W00 2023-04~12"].get("avg", -99) > 0 and r.get("avg", -99) > 0,
        r.get("avg", -99) >= cm[R12] + 0.3 and q.get("avg", -99) >= cm[N3] + 0.3 and sum(q.get("avg", -99) > x for x in ctl[N3]) >= 8,
        r.get("excess", -99) > 0 and q.get("excess", -99) > 0,
        q.get("mdd", -99) >= -15 and q.get("streak", 99) <= 12,
    ]
    print(f"{n}: 每笔 " + " ".join(f"{x:+.2f}" for x in av)
          + f" | R12 n={r.get('n')} avg={r.get('avg',0):+.2f} pf={r.get('pf')} mdd={r.get('mdd')} ex={r.get('excess',0):+.2f} streak={r.get('streak')} p05={r.get('p05')}"
          + f" | N3 n={q.get('n')} avg={q.get('avg',0):+.2f} pf={q.get('pf')} mdd={q.get('mdd')} ex={q.get('excess',0):+.2f} streak={q.get('streak')} p05={q.get('p05')}"
          + f" | {''.join('✓' if x else '✗' for x in ok)} → {'采纳' if all(ok) else '否'}")
