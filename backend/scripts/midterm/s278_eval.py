"""§278 评估：现役中长线策略 ST 口径复核。新口径(output/research/s278/*_new.json) vs 旧口径（lt_research25 族有今天数据上的旧 st 对照 r25_old.json，其余取上线时登记的值）。
采纳标准按 §278 预注册（A 类 5 条 / B 类 EA_h20 4 条 / C 类 LTV/DVLV）。"""
import json, sys, os
from statistics import median
R = "/Users/wangao/Desktop/ashare-shortline-system/backend/output/research"
P8 = ["P19 2019", "P20 2020", "P21 2021", "W0 2022-04~2023-03", "W00 2023-04~12", "W1 2024", "W2 2025-01~09", "R12 2025-10~2026-10"]
N3, R12, W00 = "N3 2024-01~2026-10", "R12 2025-10~2026-10", "W00 2023-04~12"
OLDM = json.load(open("/Users/wangao/Desktop/ashare-shortline-system/backend/app/services/midterm_backtest_metrics.json"))
def EX(d): return d.get('excess', d.get('exc', -99))
def EXN(d): return d.get('excess', d.get('exc', float('nan')))
def jl(p): return json.load(open(p)) if os.path.exists(p) else None
def critA(v):
    ex = [EX(v[p]) for p in P8]; av = [v[p]["avg"] for p in P8]; ns = [v[p]["n"] for p in P8]
    return [sum(x > 0 for x in ex) >= 6, EX(v[R12]) > 0 and EX(v[W00]) > 0,
            sum(x > 0 for x in av) >= 5, median(ns) >= 15, v[N3]["pf"] >= 1.3]
def critB(v):
    r, n = v[R12], v[N3]
    old = [P8[0], P8[1], P8[2], P8[3], P8[4]]
    return [r["avg"] > 0 and r["tot"] > 0, n["avg"] > 0 and n["tot"] > 0 and n["mdd"] > -35,
            v["W1 2024"]["avg"] > -1 and v["W2 2025-01~09"]["avg"] > -1,
            all(v[p]["avg"] > -5 and v[p]["mdd"] > -40 for p in old)]
def critA4(v):  # RP_big_h60 §91：①②③④（N3 PF 作信息）
    return critA(v)[:4]
def critBx(v):  # B 类 + 8 段超额>0 至少 5 段
    return critB(v) + [sum(EX(v[p]) > 0 for p in P8) >= 5]
def critC(v):
    r, n = v[R12], v[N3]; old = P8[:5]
    return [r["avg"] > 0 and r["tot"] > 0, n["avg"] > 0 and n["tot"] > 0 and n["mdd"] > -35,
            v["W1 2024"]["avg"] > -1 and v["W2 2025-01~09"]["avg"] > -1,
            EX(r) > 0 and EX(n) > 0 and sum(EX(v[p]) > 0 for p in P8) >= 5,
            all(v[p]["avg"] > -5 and v[p]["mdd"] > -40 for p in old),
            r["pf"] >= 1.3 or n["pf"] >= 1.3, r["n"] >= 30 and n["n"] >= 30]
mk = lambda ok: "".join("✓" if x else "✗" for x in ok)
def line(tag, v, crit):
    if v is None: return f"  {tag}: （无数据）"
    miss = [p for p in P8 + [N3] if p not in v]
    if miss: return f"  {tag}: 缺阶段 {miss}"
    ok = crit(v)
    ex = " ".join(f"{EXN(v[p]):+.2f}" for p in P8)
    f = lambda p: f"n={v[p]['n']:.0f} {v[p]['avg']:+.2f}% PF{v[p]['pf']:.2f} MDD{v[p]['mdd']:.1f} ex{EXN(v[p]):+.2f}"
    return f"  {tag}: R12[{f(R12)}] N3[{f(N3)}]\n      8段超额 {ex}  | {mk(ok)} → {'达标' if all(ok) else '不达标'}"
def old_rec(sid):
    m = OLDM.get(sid)
    if not m: return None
    ph = m["phases"]; ph = {k: v for k, v in ph.items()}
    return ph if all(p in ph for p in P8 + [N3]) else {**ph}
SPEC = [  # (sid, label, r25-var or (json, var), crit)
 ("mid-repurchase-plan", "回购预案RP_r05", ("r25", "RP_r05"), critA),
 ("mid-earnings-accel", "财报业绩加速EA_h20", ("r25", "EA_h20"), critB),
 ("mid-forecast-beat", "业绩预告首次预喜F_any_h20", ("r25", "F_any_h20"), critBx),
 ("mid-incentive-option", "期权激励INC_opt_h20", ("r25", "INC_opt_h20"), critBx),
 ("mid-block-trade-premium", "大宗溢价BT_prem_h20", ("r25", "BT_prem_h20"), critBx),
 ("long-repurchase-big", "大额回购RP_big_h60", ("r25", "RP_big_h60"), critA4),
 ("long-lowturn-lowvol", "低换手低波LTV_h60", ("r16", "LTV_h60"), critC),
 ("long-dividend-lowvol", "红利低波DVLV_h60", ("r19", "DVLV_h60"), critC),
 ("long-current-debt-down", "流动负债占比下降CLD_h60", ("r88", "CLD_h60"), critA),
 ("long-dividend-increase", "分红增加DINC_h60", ("r48", "DINC_h60"), critA),
 ("long-undist-profit-growth", "未分配利润高增UDP_h60", ("r94", "UDP_h60"), critA),
 ("long-net-cash-switch", "净负债转净现金NCS_h60", ("r98", "NCS_h60"), critA),
 ("long-growth-streak", "连续4期双增长CG4_h60", ("r102", "CG4_h60"), critA),
]
OLDRES = {"r16": None, "r19": None, "r88": "s240/res.json", "r48": "s158/res.json", "r94": "s252/res.json", "r98": "s261/res.json", "r102": "s269/res.json"}
for sid, label, (fam, var), crit in SPEC:
    print(f"## {sid}  {label}")
    new = (jl(f"{R}/s278/{fam}_new.json") or {}).get(var)
    print(line("新口径", new, crit))
    if fam == "r25":
        print(line("旧st·今天数据对照", (jl(f"{R}/s278/r25_old.json") or {}).get(var), crit))
    else:
        if OLDRES[fam]: print(line("旧(上线时登记)", (jl(f"{R}/{OLDRES[fam]}") or {}).get(var), crit))
        else:
            rec = OLDM.get(sid, {}).get("phases"); print(line("旧(上线时登记)", rec, crit) if rec and all(p in rec for p in P8 + [N3]) else "  旧(上线时登记): 见 metrics 文件")
    if new is not None and all(p in new for p in P8 + [N3]) and crit is not critA:
        try: print("  [信息] A类5条: " + mk(critA(new)))
        except Exception as e: pass
    rec = OLDM.get(sid, {}).get("phases")
    if fam == "r25" and rec:
        r, n = rec.get(R12), rec.get(N3)
        if r and n:
            print(f"  上线登记(metrics文件): R12 n={r['n']:.0f} {r['avg']:+.2f}% PF{r['pf']:.2f} MDD{r['mdd']:.1f} ex{r.get('excess', float('nan')):+.2f} | N3 n={n['n']:.0f} {n['avg']:+.2f}% PF{n['pf']:.2f} MDD{n['mdd']:.1f} ex{n.get('excess', float('nan')):+.2f}")

# RP_r05 vs 基线（lt_research18）
r18 = (jl(f"{R}/s278/r18_new.json") or {}).get("mid-repurchase-plan")
if r18 and all(p in r18 for p in P8 + [N3]):
    b = {p: r18[p]["base"] for p in r18}; v = {p: r18[p]["RP_r05"] for p in r18}
    wins = sum(1 for p in P8 if v[p]["avg"] >= b[p]["avg"])
    ok = [v[N3]["avg"] >= b[N3]["avg"], v[R12]["avg"] >= b[R12]["avg"], wins >= 5, v[N3]["pf"] >= b[N3]["pf"], v[N3]["mdd"] >= b[N3]["mdd"], v[R12]["pf"] >= 1.3]
    print("## mid-repurchase-plan RP_r05 vs 基线（lt_research18，§100 原标准）")
    g = lambda d, p: f"n={d[p]['n']:.0f} {d[p]['avg']:+.2f}% PF{d[p]['pf']:.2f} MDD{d[p]['mdd']:.1f}"
    print(f"  基线 R12[{g(b, R12)}] N3[{g(b, N3)}]\n  RP_r05 R12[{g(v, R12)}] N3[{g(v, N3)}] 8段不低于基线 {wins}/8 | {mk(ok)} → {'达标' if all(ok) else '不达标'}")
