"""§349 B 条：相对同进程原版，R12 与 N3 总收益 tot 不低于原版、N3 回撤不更深。用法：python s349_eval.py <res.json> <STRAT>"""
import json, sys
res, S = json.load(open(sys.argv[1])), sys.argv[2]
R12, N3 = "R12 2025-10~2026-10", "N3 2024-01~2026-10"
b = res[f"{S}_base"]
for v in ("TS", "DIV"):
    x = res[f"{S}_{v}"]
    ok = [x[R12]["tot"] >= b[R12]["tot"], x[N3]["tot"] >= b[N3]["tot"], x[N3]["mdd"] >= b[N3]["mdd"]]
    print(f"{S}_{v} vs base: R12 n {b[R12]['n']}→{x[R12]['n']} 每笔 {b[R12]['avg']}→{x[R12]['avg']} tot {b[R12]['tot']}→{x[R12]['tot']} mdd {b[R12]['mdd']}→{x[R12]['mdd']} | "
          f"N3 n {b[N3]['n']}→{x[N3]['n']} 每笔 {b[N3]['avg']}→{x[N3]['avg']} tot {b[N3]['tot']}→{x[N3]['tot']} mdd {b[N3]['mdd']}→{x[N3]['mdd']} | "
          f"B条 {''.join('✓' if o else '✗' for o in ok)} → {'满足' if all(ok) else '不满足'}")
