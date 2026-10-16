"""Write app/services/midterm_backtest_metrics.json for adopted mid-term strategies."""
import json, datetime
from pathlib import Path
from harness import run, metrics, PHASES
from research4 import build, C

ADOPT = {"mid-small-cap-weekly": "small_wk10_h20"}
df = build()
out = {}
for sid, cfg in ADOPT.items():
    sig, sc, fn, mh, k, mn = C[cfg]
    ph = {}
    for name, (s, e) in PHASES.items():
        m = metrics(run(df, sig, sc, fn, s, e, k_slots=999, max_new=mn, max_hold=mh), k)
        ph[name] = {a: float(v) for a, v in m.items()}
    out[sid] = {"recent_12m": ph["R12 2025-10~2026-10"], "phases": ph, "config": cfg,
                "window": "2025-10-01~2026-10-13", "generated": datetime.date.today().isoformat(),
                "note": "成本0.3%/笔；mdd 为按批次平仓结算的组合回撤，不含持有期内浮亏"}
    print(sid, out[sid]["recent_12m"])
Path(__file__).resolve().parents[2].joinpath("app/services/midterm_backtest_metrics.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
