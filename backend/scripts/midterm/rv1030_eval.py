"""§RV B 条（引擎口径相对同进程原版）：R12/N3 每笔 ≥ 原版；8 段 ≥6 段 ≥ 原版且 W0 ≥ 原版；N3 回撤不深于原版。"""
import json, sys
v = json.load(open(sys.argv[1]))
P8 = ["P19 2019", "P20 2020", "P21 2021", "W0 2022-04~2023-03", "W00 2023-04~12", "W1 2024", "W2 2025-01~09", "R12 2025-10~2026-10"]
R12, N3 = P8[-1], "N3 2024-01~2026-10"
for S, cands in (("EA", ["EA_d0", "EA_mv", "EA_combo", "EA_first"]), ("FA", ["FA_mv", "FA_d0"])):
    b = v[f"{S}_base"]
    for n in cands:
        x = v[n]; ge = [x[p]["avg"] >= b[p]["avg"] for p in P8]
        ok = [x[R12]["avg"] >= b[R12]["avg"] and x[N3]["avg"] >= b[N3]["avg"], sum(ge) >= 6 and x[P8[3]]["avg"] >= b[P8[3]]["avg"], x[N3]["mdd"] >= b[N3]["mdd"]]
        print(f"B {n}: ≥原版段 {sum(ge)}/8 ({''.join('✓' if g else '✗' for g in ge)}) R12 {x[R12]['avg']:+.2f} vs {b[R12]['avg']:+.2f} N3 {x[N3]['avg']:+.2f} vs {b[N3]['avg']:+.2f} "
              f"N3回撤 {x[N3]['mdd']} vs {b[N3]['mdd']} N3胜率 {x[N3]['win']} vs {b[N3]['win']} → {''.join('✓' if o else '✗' for o in ok)} {'B过' if all(ok) else 'B不过'}")
