"""§987–§991 评估（§282 短线标准，预注册写死）。
判定：1) R12、N3 扣成本后每笔 >0；2) PF≥1.3（两窗）；3) 最大回撤不深于 −35%（两窗）；
      4) 4 窗口（W0 / W00 / EXT 2024-01~2025-09 / R12）至少 3 段每笔为正，且 W0、W00 每笔 ≥ −5%；5) R12、N3 各 ≥30 笔。
展示（不作门槛）：8 段每笔、同市值超额、收益来源（两端各去5%、去最赚5%、前10笔占利润、中位数、稳健/彩票）、每持有日收益。
用法：python saf_s987_eval.py <res.json> [名字...]"""
import json, sys, os
import polars as pl
res_path = sys.argv[1]
allres = json.load(open(res_path)); d = os.path.dirname(res_path)
names = sys.argv[2:] or [k for k in allres if not k.startswith("_")]
P8 = ["P19 2019", "P20 2020", "P21 2021", "W0 2022-04~2023-03", "W00 2023-04~12", "W1 2024", "W2 2025-01~09", "R12 2025-10~2026-10"]
W4 = ["W0 2022-04~2023-03", "W00 2023-04~12", "EXT 2024-01~2025-09", "R12 2025-10~2026-10"]
R12, N3 = "R12 2025-10~2026-10", "N3 2024-01~2026-10"
MDD_CAP = 35.0


def quality(name, tag):
    f = os.path.join(d, f"trades_{name}_{tag}.csv")
    if not os.path.exists(f): return {}
    r = pl.read_csv(f)["ret"].sort(descending=True).to_list(); n = len(r)
    k = max(1, round(0.05 * n)); tot = sum(r)
    trim5 = 100 * sum(r[k:]) / max(1, n - k)
    tb5 = 100 * sum(r[k:n - k]) / (n - 2 * k) if n > 2 * k else None
    sr = sorted(r)
    med = 100 * sr[n // 2] if n % 2 else 100 * (sr[n // 2 - 1] + sr[n // 2]) / 2
    top10 = (sum(r[:10]) / tot) if tot > 0 else None
    lab = "稳健型" if (tb5 is not None and tb5 > 0 and med > 0) else "彩票型"
    return {"n": n, "trim_both5": None if tb5 is None else round(tb5, 2), "trim5": round(trim5, 2), "median": round(med, 2),
            "top10_share": None if top10 is None else round(100 * top10, 1), "label": lab}


def g(v, p, k, dflt=-99.0):
    x = v.get(p, {}).get(k)
    return dflt if x is None else x


summary = {}
for name in names:
    v = allres[name]
    a8 = [g(v, p, "avg") if g(v, p, "n", 0) > 0 else None for p in P8]
    a4 = [g(v, p, "avg") if g(v, p, "n", 0) > 0 else -99.0 for p in W4]
    r, q = v.get(R12, {}), v.get(N3, {})
    ok = [g(v, R12, "avg") > 0 and g(v, N3, "avg") > 0,
          g(v, R12, "pf", 0) >= 1.3 and g(v, N3, "pf", 0) >= 1.3,
          g(v, R12, "mdd") >= -MDD_CAP and g(v, N3, "mdd") >= -MDD_CAP,
          sum(x > 0 for x in a4) >= 3 and a4[0] >= -5 and a4[1] >= -5,
          g(v, R12, "n", 0) >= 30 and g(v, N3, "n", 0) >= 30]
    qr, qn = quality(name, "R12"), quality(name, "N3")
    lab = "稳健型" if qr.get("label") == "稳健型" and qn.get("label") == "稳健型" else "彩票型"
    perday = {t: (round(g(v, p, "avg") / g(v, p, "days"), 3) if g(v, p, "days", 0) > 0 else None) for t, p in (("R12", R12), ("N3", N3))}
    print(f"{name}: 8段每笔 " + " ".join(f"{p.split()[0]} {'无' if x is None else f'{x:+.2f}'}" for p, x in zip(P8, a8))
          + f" | 8段正 {sum((x or 0) > 0 for x in a8)}/{sum(x is not None for x in a8)}")
    print("  4窗口 " + " ".join(f"{p.split()[0]} {x:+.2f}" for p, x in zip(W4, a4)) + f" | 正 {sum(x > 0 for x in a4)}/4")
    print("  n " + " ".join(f"{p.split()[0]} {int(g(v, p, 'n', 0))}" for p in P8 + ["EXT 2024-01~2025-09", N3]))
    for tag, m, qq in (("R12", r, qr), ("N3", q, qn)):
        print(f"  {tag}: n={m.get('n')} win={m.get('win')} avg={m.get('avg')} pf={m.get('pf')} mdd={m.get('mdd')} days={m.get('days')} "
              f"每持有日={perday[tag]} ex={m.get('excess')} streak={m.get('streak')} p05={m.get('p05')} | 质量 {qq}")
    print(f"  判定 {''.join('✓' if x else '✗' for x in ok)} → {'达标/采纳' if all(ok) else '否决'} | 标签 {lab}")
    summary[name] = {"ok": ok, "pass": all(ok), "label": lab, "perday": perday, "q_R12": qr, "q_N3": qn,
                     "avg8": dict(zip([p.split()[0] for p in P8], a8)), "avg4": dict(zip([p.split()[0] for p in W4], a4))}
json.dump(summary, open(os.path.join(d, "eval_summary.json"), "w"), indent=1, ensure_ascii=False)
