"""§353：RP_combo vs RP_base。A=§282 中线；B=相对 base。"""
from __future__ import annotations
import json, sys
from pathlib import Path
B = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(B / "scripts/midterm"))
from s286_287_eval import eval_one  # may not exist as import — fallback below

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else B / "output/research/s353/res.json"
res = json.loads(OUT.read_text())
# reuse CLI
import subprocess
py = B / ".venv/bin/python"
for n in ("RP_base", "RP_combo"):
    print("====", n, flush=True)
    subprocess.check_call([str(py), str(B / "scripts/midterm/s286_287_eval.py"), str(OUT), n, "25"])

# relative B
PH8 = [k for k in next(iter(res.values())) if k.startswith(("P1", "W0", "W1", "W2", "R12"))]
# find keys
base, comb = res["RP_base"], res["RP_combo"]

def pick(d, *cands):
    for k in d:
        for c in cands:
            if c in k:
                return d[k]
    return None

b_r12, c_r12 = pick(base, "R12"), pick(comb, "R12")
b_n3, c_n3 = pick(base, "N3"), pick(comb, "N3")
print("BASE R12", b_r12)
print("COMBO R12", c_r12)
print("BASE N3", b_n3)
print("COMBO N3", c_n3)

def avg(m):
    return m.get("avg") if m else None

ok_b = True
# phases: every phase avg(combo) >= avg(base)
phase_ok = []
for k in sorted(base.keys()):
    if k.startswith("N3"): continue
    if "R12" in k or k.startswith("P") or k.startswith("W"):
        ba, ca = avg(base[k]), avg(comb[k])
        if ba is None or ca is None:
            continue
        phase_ok.append((k, ba, ca, ca >= ba - 1e-9))
print("phases", phase_ok)
rel = (
    avg(c_r12) is not None and avg(b_r12) is not None and avg(c_r12) >= avg(b_r12)
    and avg(c_n3) is not None and avg(b_n3) is not None and avg(c_n3) >= avg(b_n3)
    and (c_n3.get("mdd") is not None and b_n3.get("mdd") is not None and c_n3["mdd"] >= b_n3["mdd"])
    and all(x[3] for x in phase_ok)
)
print("REL_B", rel)
Path(OUT).with_name("eval_rel.txt").write_text(json.dumps({"phase_ok": phase_ok, "rel_b": rel, "b_r12": b_r12, "c_r12": c_r12, "b_n3": b_n3, "c_n3": c_n3}, ensure_ascii=False, indent=1, default=float))
