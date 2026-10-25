"""§286/§287 评估：按 §282 新达标标准（用户 2026-10-25 15:11 批准）+ 收益来源指标（§285：标签按两头各去 5% 的 trim_both5 判定，trim5 只去最赚 5% 为压力测试）。
用法：python s283_284_eval.py <res.json> <策略名> <mdd上限(正数,中线25/短线35)>"""
import json, sys, os
import polars as pl
res_path, name, mdd_cap = sys.argv[1], sys.argv[2], float(sys.argv[3])
v = json.load(open(res_path))[name]; d = os.path.dirname(res_path)
P8 = ["P19 2019", "P20 2020", "P21 2021", "W0 2022-04~2023-03", "W00 2023-04~12", "W1 2024", "W2 2025-01~09", "R12 2025-10~2026-10"]
R12, N3 = "R12 2025-10~2026-10", "N3 2024-01~2026-10"


def quality(tag):
    f = os.path.join(d, f"trades_{name}_{tag}.csv")
    if not os.path.exists(f): return {}
    r = pl.read_csv(f)["ret"].sort(descending=True).to_list(); n = len(r)
    k = max(1, round(0.05 * n)); tot = sum(r)
    trim5 = 100 * sum(r[k:]) / max(1, n - k)
    tb5 = 100 * sum(r[k:n - k]) / (n - 2 * k) if n > 2 * k else None
    med = 100 * sorted(r)[n // 2] if n % 2 else 100 * (sorted(r)[n // 2 - 1] + sorted(r)[n // 2]) / 2
    top10 = (sum(r[:10]) / tot) if tot > 0 else None
    lab = "稳健型" if (tb5 is not None and tb5 > 0 and med > 0) else "彩票型"
    return {"n": n, "trim_both5": None if tb5 is None else round(tb5, 2), "trim5": round(trim5, 2), "median": round(med, 2), "top10_share": None if top10 is None else round(100 * top10, 1), "label": lab}


av = [v.get(p, {}).get("avg", -99) for p in P8]; ex = [v.get(p, {}).get("excess", -99) for p in P8]
r, q = v[R12], v[N3]
ok = [r.get("avg", -99) > 0 and q.get("avg", -99) > 0,
      r.get("pf", 0) >= 1.3 and q.get("pf", 0) >= 1.3,
      r.get("mdd", -99) >= -mdd_cap and q.get("mdd", -99) >= -mdd_cap,
      sum(x > 0 for x in av) >= 6 and v["W0 2022-04~2023-03"].get("avg", -99) >= -5 and v["W00 2023-04~12"].get("avg", -99) >= -5,
      r.get("n", 0) >= 30 and q.get("n", 0) >= 30]
qr, qn = quality("R12"), quality("N3")
lab = "稳健型" if qr.get("label") == "稳健型" and qn.get("label") == "稳健型" else "彩票型"
print(f"{name}: 每笔 " + " ".join(f"{p.split()[0]} {x:+.2f}" for p, x in zip(P8, av)) + f" | 正段 {sum(x > 0 for x in av)}/8")
print("  n " + " ".join(f"{p.split()[0]} {int(v.get(p, {}).get('n', 0))}" for p in P8))
print("  超额(参考) " + " ".join(f"{p.split()[0]} {x:+.2f}" for p, x in zip(P8, ex)) + f" | 正 {sum(x > 0 for x in ex)}/8")
for tag, m, qq in (("R12", r, qr), ("N3", q, qn)):
    print(f"  {tag}: n={m.get('n')} win={m.get('win')} avg={m.get('avg'):+.2f} pf={m.get('pf')} mdd={m.get('mdd')} tot={m.get('tot')} ex={m.get('excess', 0):+.2f} streak={m.get('streak')} p05={m.get('p05')} | 质量 {qq}")
print(f"  判定 {''.join('✓' if x else '✗' for x in ok)} → {'达标/采纳' if all(ok) else '否决'} | 标签 {lab}")
