"""§337 判定（预注册写定）。所有每笔/PF/中位数/收益来源只用满期卖出的笔（complete=True）；未满期的笔单列。
回撤用引擎逐日盯市口径（含未满期笔的浮动盈亏）。
A 标准全套（中线）：R12 与 N3 每笔 >0、PF ≥1.3、回撤不深于 −25%、各 ≥30 笔；8 段 ≥6 段为正、W0/W00 ≥ −5%。
B 未看过数据为主（近 12 个月只展示、不当证据）：
  B1 除 R12 外的 7 段（P19/P20/P21/W0/W00/W1/W2）至少 6 段每笔为正；
  B2 样本外合并 OOS（2019-01~2025-09 信号）每笔 >0 且 PF ≥1.3；
  B3 N3 前两年（2024-01~2025-09 信号，取 N3 逐笔按入场日过滤）每笔 >0、PF ≥1.3、≥30 笔。
C 新增价值（F_any_dn 是原策略子集）：OOS 与 N3 前两年每笔都比原策略 F_any_h20 高 ≥1.0 个百分点，否则写「没有新增价值」。
D 反向（只留首日收涨 F_any_up）相对原策略：OOS / N3 前两年每笔、PF、回撤、笔数的变化（只报告）。
通过 = A 全过 且 B1–B3 全过 且 C 过。
用法：python s337_eval.py <res.json>"""
import json, os, sys
import polars as pl
res = json.load(open(sys.argv[1])); d = os.path.dirname(sys.argv[1])
P8 = ["P19 2019", "P20 2020", "P21 2021", "W0 2022-04~2023-03", "W00 2023-04~12", "W1 2024", "W2 2025-01~09", "R12 2025-10~2026-10"]
N3, OOS = "N3 2024-01~2026-10", "OOS 2019-01~2025-09"
NAMES = ("F_any_h20", "F_any_dn_h20", "F_any_up_h20")


def load(n, tag):
    f = os.path.join(d, f"trades_{n}_{tag}.csv")
    if not os.path.exists(f):
        return None
    return pl.read_csv(f, schema_overrides={"code": pl.Utf8, "entry": pl.Utf8, "exit": pl.Utf8})


def stats(t):
    if t is None or t.height == 0:
        return {"n": 0}
    r = t.filter(pl.col("complete"))["ret"].to_list()
    unf = t.height - len(r)
    if not r:
        return {"n": 0, "unfinished": unf}
    n = len(r); rs = sorted(r, reverse=True); k = max(1, round(0.05 * n)); tot = sum(r)
    w = sum(x for x in r if x > 0); l = -sum(x for x in r if x < 0)
    s = sorted(r); med = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    tb5 = 100 * sum(rs[k:n - k]) / (n - 2 * k) if n > 2 * k else None
    out = {"n": n, "unfinished": unf, "avg": round(100 * tot / n, 2), "pf": round(w / l, 2) if l else 99.0,
           "win": round(100 * sum(x > 0 for x in r) / n, 1), "median": round(100 * med, 2),
           "trim_both5": None if tb5 is None else round(tb5, 2), "trim5": round(100 * sum(rs[k:]) / max(1, n - k), 2),
           "top10_share": round(100 * sum(rs[:10]) / tot, 1) if tot > 0 else None}
    out["robust"] = bool(tb5 is not None and tb5 > 0 and med > 0)
    return out


def mdd(n, ph):
    return res.get(n, {}).get(ph, {}).get("mdd")


S = {}
for n in NAMES:
    S[n] = {ph.split()[0]: stats(load(n, ph.split()[0])) for ph in P8 + [N3, OOS]}
    n3 = load(n, "N3")
    S[n]["N3pre"] = stats(n3.filter(pl.col("entry") < "2025-10-01")) if n3 is not None else {"n": 0}
    oos = load(n, "OOS")
    S[n]["by_year"] = {y: stats(oos.filter(pl.col("entry").str.slice(0, 4) == y)) for y in ("2019", "2020", "2021", "2022", "2023", "2024", "2025")} if oos is not None else {}

for n in NAMES:
    v = S[n]
    print(f"== {n}")
    print("  8 段每笔（满期）：" + " ".join(f"{p.split()[0]} {v[p.split()[0]].get('avg')}({v[p.split()[0]].get('n')})" for p in P8))
    print("  分年（OOS 运行，按入场年）：" + " | ".join(f"{y}: n={x.get('n')} 每笔 {x.get('avg')} PF {x.get('pf')} 中位 {x.get('median')}" for y, x in v["by_year"].items()))
    for tag, key, ph in (("OOS 2019-01~2025-09", "OOS", OOS), ("N3 前两年 2024-01~2025-09", "N3pre", N3), ("N3", "N3", N3), ("R12（只展示）", "R12", P8[-1])):
        print(f"  {tag}: {v[key]} 回撤 {mdd(n, ph)}")

f = S["F_any_dn_h20"]; base = S["F_any_h20"]
p7 = [f[p.split()[0]].get("avg", -99) for p in P8[:-1]]
A = [f["R12"].get("avg", -99) > 0 and f["N3"].get("avg", -99) > 0,
     f["R12"].get("pf", 0) >= 1.3 and f["N3"].get("pf", 0) >= 1.3,
     (mdd("F_any_dn_h20", P8[-1]) or -99) >= -25 and (mdd("F_any_dn_h20", N3) or -99) >= -25,
     sum(f[p.split()[0]].get("avg", -99) > 0 for p in P8) >= 6 and f["W0"].get("avg", -99) >= -5 and f["W00"].get("avg", -99) >= -5,
     f["R12"].get("n", 0) >= 30 and f["N3"].get("n", 0) >= 30]
B = [sum(x > 0 for x in p7) >= 6,
     f["OOS"].get("avg", -99) > 0 and f["OOS"].get("pf", 0) >= 1.3,
     f["N3pre"].get("avg", -99) > 0 and f["N3pre"].get("pf", 0) >= 1.3 and f["N3pre"].get("n", 0) >= 30]
C = all(f[k].get("avg", -99) >= base[k].get("avg", 99) + 1.0 for k in ("OOS", "N3pre"))
print("A 标准全套 " + "".join("✓" if x else "✗" for x in A) + f" | B 未看过数据 " + "".join("✓" if x else "✗" for x in B)
      + f"（7 段正 {sum(x > 0 for x in p7)}/7）| C 新增价值 {'✓' if C else '✗（没有新增价值）'}")
print(f"§337 判定：{'通过（先不上线，报上线方案）' if all(A) and all(B) and C else '否决'}")
u = S["F_any_up_h20"]
for k, ph in (("OOS", OOS), ("N3pre", N3)):
    print(f"D 只留首日收涨 vs 原策略 [{k}]：每笔 {base[k].get('avg')}→{u[k].get('avg')} PF {base[k].get('pf')}→{u[k].get('pf')} "
          f"笔数 {base[k].get('n')}→{u[k].get('n')} 回撤 {mdd('F_any_h20', ph)}→{mdd('F_any_up_h20', ph)}")
json.dump(S, open(os.path.join(d, "eval_s337.json"), "w"), ensure_ascii=False, indent=1)
