"""[长线/中线] round 3: forecast-drift grid (threshold x hold x gate), robustness count."""
import json, itertools
import polars as pl
from lt_research import build, forecast_sig, signals, fixed, PH
from lt_research2 import regime
from realistic import run_real, metrics_real
c = pl.col
df = regime(signals(forecast_sig(build())))
fcb = c("liq") & c("fc_min").is_not_null()
res = {}
for thr, rmax, h, gate in itertools.product(*([(50,), (0.15,), (20,), ("g20",)] if len(__import__("sys").argv) > 1 else [(30, 50, 100), (0.15, 0.25), (10, 20), ("g20", "g20x5")])):
    g = c("ewi") > c("e20")
    if gate == "g20x5": g = g & (c("ewi") > c("ewi").shift(5).over("code"))  # rising
    sig = (fcb & (c("fc_min") >= thr) & (c("r20") < rmax) & g).fill_null(False)
    d = df.with_columns(sig.alias("X"))
    n = f"fc{thr}_r{rmax}_h{h}_{gate}"; row = {}
    for ph, (s, e) in PH.items():
        t, cur = run_real(d, "X", "sc_fc", fixed(h), s, e, k_slots=20, max_new=5, max_hold=h)
        row[ph] = {a: float(v) for a, v in metrics_real(t, cur).items()}
    pos = sum(1 for v in row.values() if v.get("avg", -1) > 0)
    worst = min(v.get("tot", 0) for v in row.values())
    print(n, "pos", pos, "/8 worst_tot", worst, "R12", row["R12 2025-10~2026-10"], flush=True)
    res[n] = row
json.dump(res, open("/tmp/lt3best.json" if len(__import__("sys").argv) > 1 else "/tmp/lt3.json", "w"), indent=1)
