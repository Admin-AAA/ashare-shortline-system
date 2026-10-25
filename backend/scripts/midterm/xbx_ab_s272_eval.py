"""§272/§273 评估：按预注册标准逐条判定。"""
import json, sys
import numpy as np
res = json.load(open(sys.argv[1]))
P8 = ["P19 2019", "P20 2020", "P21 2021", "W0 2022-04~2023-03", "W00 2023-04~12", "W1 2024", "W2 2025-01~09", "R12 2025-10~2026-10"]
N3, R12, W00 = "N3 2024-01~2026-10", "R12 2025-10~2026-10", "W00 2023-04~12"
L = res["live"]
def ctrl(prefix):
    return {ph: [res[f"{prefix}_{s}"][ph] for s in range(20)] for ph in res["live"]}
def judge(name, kind, rand):
    v = res[name]; C = ctrl(rand)
    ok = {}
    if kind == "cal":
        ok["1 N3均值≥live+0.5 且 R12≥live"] = v[N3]["avg"] >= L[N3]["avg"] + 0.5 and v[R12]["avg"] >= L[R12]["avg"]
        ok["2 笔数 N3≥50 R12≥20"] = v[N3]["n"] >= 50 and v[R12]["n"] >= 20
        ok["3 N3 PF≥live 且 R12 PF≥1.3"] = v[N3]["pf"] >= L[N3]["pf"] and v[R12]["pf"] >= 1.3
    else:
        ok["1 N3均值≥live+0.5 且 R12≥live"] = v[N3]["avg"] >= L[N3]["avg"] + 0.5 and v[R12]["avg"] >= L[R12]["avg"]
        ok["2 N3 PF≥live,R12 PF≥1.3,笔数达标"] = v[N3]["pf"] >= L[N3]["pf"] and v[R12]["pf"] >= 1.3 and v[N3]["n"] >= 50 and v[R12]["n"] >= 20
    ge = sum(v[p]["avg"] >= L[p]["avg"] for p in P8)
    ok[f"4 8段均值≥live 至少6段且W00≥live (实际{ge}/8)"] = ge >= 6 and v[W00]["avg"] >= L[W00]["avg"]
    beat = sum(v[p]["avg"] > np.mean([x["avg"] for x in C[p]]) for p in P8)
    n3b = sum(v[N3]["avg"] > x["avg"] for x in C[N3])
    ok[f"5 胜随机对照 ≥5/8段且N3≥18/20套 (实际{beat}/8, {n3b}/20)"] = beat >= 5 and n3b >= 18
    if kind == "cal":
        ok["6 N3 tot≥live,MDD不更深,连亏≤live+1,p5不差"] = (v[N3]["tot"] >= L[N3]["tot"] and v[N3]["mdd"] >= L[N3]["mdd"]
                                                      and v[N3]["streak"] <= L[N3]["streak"] + 1 and v[N3]["p5"] >= L[N3]["p5"])
        ok["7 剔2024-01~02后N3均值≥live+0.2"] = v[N3]["avg_ex"] >= L[N3]["avg_ex"] + 0.2
    else:
        ok["5b N3与R12的p5都不比live差,MDD不更深,连亏≤live+1"] = (v[N3]["p5"] >= L[N3]["p5"] and v[R12]["p5"] >= L[R12]["p5"]
                                                      and v[N3]["mdd"] >= L[N3]["mdd"] and v[N3]["streak"] <= L[N3]["streak"] + 1)
    return ok
print("live: " + " ".join(f"{p[:3]} n={L[p]['n']:.0f} avg={L[p]['avg']:+.2f} pf={L[p]['pf']}" for p in (R12, N3)), "N3 mdd", L[N3]["mdd"], "streak", L[N3]["streak"], "p5", L[N3]["p5"], "tot", L[N3]["tot"])
for name, kind, rand in (("CAL_A", "cal", "CAL_RAND_A"), ("CAL_B", "cal", "CAL_RAND_B"), ("FIN_A", "fin", "FIN_RAND_A"), ("FIN_B", "fin", "FIN_RAND_B")):
    v = res[name]; C = ctrl(rand)
    print(f"\n== {name} ==")
    for p in (R12, N3):
        print(f"  {p[:3]} n={v[p]['n']:.0f} win={v[p]['win']} avg={v[p]['avg']:+.2f} pf={v[p]['pf']} mdd={v[p]['mdd']} tot={v[p]['tot']} streak={v[p]['streak']:.0f} p5={v[p]['p5']} avg_ex={v[p]['avg_ex']}"
              f" | live avg={L[p]['avg']:+.2f} p5={L[p]['p5']} | 随机均值 {np.mean([x['avg'] for x in C[p]]):+.2f} (min {min(x['avg'] for x in C[p]):+.2f} max {max(x['avg'] for x in C[p]):+.2f}) n̄={np.mean([x['n'] for x in C[p]]):.0f}")
    print("  各段均值 " + " ".join(f"{p[:3]}:{v[p]['avg']:+.2f}/{L[p]['avg']:+.2f}" for p in P8))
    ok = judge(name, kind, rand)
    for k, b in ok.items(): print(f"  {'✓' if b else '✗'} {k}")
    print("  →", "采纳" if all(ok.values()) else "否决")
for k in ("skipped_A", "skipped_B", "removed_A", "removed_B"):
    print(k, "R12 n=%d avg=%+.2f | N3 n=%d avg=%+.2f" % (res[k][R12]["n"], res[k][R12]["avg"], res[k][N3]["n"], res[k][N3]["avg"]))
