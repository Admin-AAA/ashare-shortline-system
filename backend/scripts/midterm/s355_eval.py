"""§355 相对 B：BT_inst_h20 vs BT_prem_h20（R12、N3 单笔 ≥ base 且 N3 MDD 不更深）。"""
import json, sys
res = json.load(open(sys.argv[1]))
b, x = res["BT_prem_h20"], res["BT_inst_h20"]
def g(d, k): return next(v for kk, v in d.items() if kk.startswith(k))
ok = True
for k in ("R12", "N3"):
    bb, xx = g(b, k), g(x, k)
    print(k, "base", bb, "\n   inst", xx)
    ok &= (xx.get("avg") or -99) >= (bb.get("avg") or -99)
ok &= g(x, "N3")["mdd"] >= g(b, "N3")["mdd"]
print("B_PASS" if ok else "B_FAIL")
