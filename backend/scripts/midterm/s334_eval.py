"""§334 判定（预注册写定）：F_any_nd_h20 自己过 §282 中线全部上线标准（由 s286_287_eval.py 判，回撤上限 25），
并且相对原策略 F_any_h20：R12 与 N3 每笔都不低于原策略、回撤不更深、笔数减少不超过 30%。
用法：python s334_eval.py <res.json>"""
import json, sys
res = json.load(open(sys.argv[1]))
R12, N3 = "R12 2025-10~2026-10", "N3 2024-01~2026-10"
P8 = ["P19 2019", "P20 2020", "P21 2021", "W0 2022-04~2023-03", "W00 2023-04~12", "W1 2024", "W2 2025-01~09", R12]
a, b = res["F_any_h20"], res["F_any_nd_h20"]
print("每笔（原 → 改）：" + " ".join(f"{p.split()[0]} {a[p].get('avg')}→{b[p].get('avg')}" for p in P8 + [N3]))
ok = []
for p in (R12, N3):
    x, y = a[p], b[p]
    c1 = y["avg"] >= x["avg"]; c2 = y["mdd"] >= x["mdd"]; c3 = y["n"] >= 0.7 * x["n"]
    ok += [c1, c2, c3]
    print(f"  {p.split()[0]}: 笔数 {x['n']}→{y['n']}（{100 * (y['n'] / x['n'] - 1):+.0f}%）每笔 {x['avg']}→{y['avg']} PF {x['pf']}→{y['pf']} 回撤 {x['mdd']}→{y['mdd']} "
          f"每持有日 {x.get('per_day')}→{y.get('per_day')} | 被否原成交 {x.get('vetoed')} | 每笔不降{'✓' if c1 else '✗'} 回撤不深{'✓' if c2 else '✗'} 笔数≥70%{'✓' if c3 else '✗'}")
print(f"§334 相对原策略条件：{'全部满足' if all(ok) else '不满足'}")
