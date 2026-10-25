"""§257 评估：预注册标准 ①~⑥。"""
import json, sys
from statistics import median
res = json.load(open(sys.argv[1]))
P8 = ["P19 2019", "P20 2020", "P21 2021", "W0 2022-04~2023-03", "W00 2023-04~12", "W1 2024", "W2 2025-01~09", "R12 2025-10~2026-10"]
N3, R12, W00 = "N3 2024-01~2026-10", P8[-1], P8[4]
ctrls = {"PB": ["CTRL_PB_FOLLOW", "CTRL_PB_MKT"], "SD": ["CTRL_SD"]}
for key, v in res.items():
    if key.endswith("|seeds"):
        continue
    name = key.split("|")[0]
    av = [v.get(p, {}).get("avg", -99) for p in P8]; ns = [v.get(p, {}).get("n", 0) for p in P8]
    line = f"{key}: 每笔 " + " ".join(f"{x:+.2f}" for x in av) + f" | n " + "/".join(str(int(x)) for x in ns)
    g = v.get(N3, {}); r = v.get(R12, {})
    line += f" | N3 n={g.get('n',0)} avg={g.get('avg',0):+.2f} pf={g.get('pf',0)} win={g.get('win',0)} mdd={g.get('mdd',0)} 连亏{g.get('max_loss_streak',0)}"
    line += f" | R12 n={r.get('n',0)} avg={r.get('avg',0):+.2f} pf={r.get('pf',0)} win={r.get('win',0)} mdd={r.get('mdd',0)}"
    if name in ("PB1", "PB2", "SD1", "SD2") and key.endswith("|main"):
        cs = ctrls["PB" if name.startswith("PB") else "SD"]
        beats = [sum(v.get(p, {}).get("avg", -99) > res[c_ + "|main"].get(p, {}).get("avg", -99) for p in P8) for c_ in cs]
        ok = [sum(x > 0 for x in av) >= 6, r.get("avg", -99) > 0 and v.get(W00, {}).get("avg", -99) > 0, g.get("pf", 0) >= 1.3,
              all(b >= 5 for b in beats), g.get("n", 0) >= 60 and r.get("n", 0) >= 30, g.get("mdd", -99) >= -30]
        line += f" | 胜对照 {beats} | {''.join('✓' if x else '✗' for x in ok)} → {'采纳' if all(ok) else '否'}"
    print(line)
