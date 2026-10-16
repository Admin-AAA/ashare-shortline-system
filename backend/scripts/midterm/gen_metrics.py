"""Write app/services/midterm_backtest_metrics.json (realistic engine: limit-up no buy, locked
limit-down carry, mark-to-market MDD, K=20 notional slots)."""
import json, datetime
from pathlib import Path
import polars as pl
from harness import PHASES
from research5 import build5, fixed
from research7 import add_simple_guard
from realistic import run_real, metrics_real

df = add_simple_guard(build5()); c = pl.col
wk = c("trade_date").str.to_date().dt.weekday() == 5
df = df.with_columns((wk & (c("rk_mv") <= 10) & (c("sciS") > c("sciS20"))).fill_null(False).alias("S"))
ph = {}
for name, (s, e) in PHASES.items():
    t, cur = run_real(df, "S", "sc_small", fixed(20), s, e, k_slots=20, max_new=10, max_hold=20)
    ph[name] = {a: float(v) for a, v in metrics_real(t, cur).items()}
    print(name, ph[name], flush=True)
out = {"mid-small-cap-weekly": {"recent_12m": ph["R12 2025-10~2026-10"], "phases": ph, "config": "research7 top10 sciS>MA20 hold20 K20",
       "window": "2025-10-01~2026-10-13", "generated": datetime.date.today().isoformat(),
       "note": "成本0.3%/笔；开盘涨停不买、跌停封死顺延卖出；最大回撤按逐日盯市（20个等额仓位）计算"}}
Path(__file__).resolve().parents[2].joinpath("app/services/midterm_backtest_metrics.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
