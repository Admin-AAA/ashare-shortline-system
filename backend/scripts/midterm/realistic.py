"""[中长线] Realistic mid-term backtest: no buy on limit-up open, can't sell when locked limit-down
(carry to next day), mark-to-market daily equity for MDD. Fixed notional 1/K per position."""
from __future__ import annotations
from pathlib import Path
from collections import defaultdict
import numpy as np, polars as pl

DATA = Path(__file__).resolve().parents[2] / "data/midterm"
COST = 0.003

def add_limits(df: pl.DataFrame) -> pl.DataFrame:
    lim = pl.read_parquet(DATA / "stk_limits.parquet").select("code", "trade_date", "up_limit", "down_limit")
    df = df.join(lim, on=["code", "trade_date"], how="left")
    c = pl.col
    # fallback when limits missing: +-9.8% (main) / 19.8% (300/688) from prev close
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    pc = c("close").shift(1).over("code")
    return df.with_columns(
        (c("open") >= pl.coalesce(c("up_limit"), pc * (1 + band)) - 0.005).fill_null(False).alias("lim_up_open"),
        (c("high") <= pl.coalesce(c("down_limit"), pc * (1 - band)) + 0.005).fill_null(False).alias("lock_down"),
    ).sort("code", "trade_date")

def run_real(df, sig_col, score_col, exit_fn, start, end, k_slots=10, max_new=10, max_hold=20, guard_col=None):
    """exit_fn(a, e, i) at close i -> sell at open i+1 (carried while locked limit-down).
    guard_col: boolean column; True at close i forces exit of all holdings at next open."""
    dates = sorted(df.filter(pl.col("trade_date").is_between(pl.lit(start), pl.lit(end)))["trade_date"].unique().to_list())
    if not dates: return None, None
    sub = df.filter(pl.col("trade_date") >= start)
    codes = {code: {col: part[col].to_numpy() for col in part.columns if col != "code"}
             for (code,), part in sub.partition_by("code", as_dict=True, maintain_order=True).items()}
    idx = {c: {d: i for i, d in enumerate(a["trade_date"])} for c, a in codes.items()}
    by_date = defaultdict(list)
    for code, d, s in (sub.filter(pl.col(sig_col) & (pl.col("trade_date") <= end))
                       .select("code", "trade_date", score_col).sort(["trade_date", score_col], descending=[False, True]).iter_rows()):
        by_date[d].append(code)
    guard = set()
    if guard_col:
        guard = set(sub.filter(pl.col(guard_col))["trade_date"].unique().to_list())
    held = {}
    trades = []
    pnl = defaultdict(float)  # date -> pnl in units of position notional
    for d in dates:
        held = {c: x for c, x in held.items() if x > d}
        if len(held) >= k_slots: continue
        for c in [c for c in by_date.get(d, []) if c not in held][:max_new]:
            if len(held) >= k_slots: break
            a = codes[c]; n = len(a["trade_date"]); i0 = idx[c][d]; e = i0 + 1
            if e >= n or a["lim_up_open"][e]: continue
            px = a["ao"][e]; xi = None
            for i in range(e, min(n - 1, e + max_hold)):
                if exit_fn(a, e, i) or a["trade_date"][i] in guard:
                    xi = i + 1; break
            if xi is None: xi = min(n - 1, e + max_hold)
            while xi < n - 1 and a["lock_down"][xi]: xi += 1  # can't sell
            # mark to market: day e: close vs entry open; then close-to-close; exit day: open vs prev close
            prev = px
            for j in range(e, xi):
                pnl[a["trade_date"][j]] += (a["ac"][j] - prev) / px; prev = a["ac"][j]
            pnl[a["trade_date"][xi]] += (a["ao"][xi] - prev) / px - COST
            trades.append((c, a["trade_date"][e], a["trade_date"][xi], xi - e, a["ao"][xi] / px - 1 - COST))
            held[c] = a["trade_date"][xi]
    t = pl.DataFrame(trades, schema=["code", "entry", "exit", "days", "ret"], orient="row")
    ds = sorted(pnl)
    eq = 1 + np.cumsum([pnl[x] / k_slots for x in ds])
    return t, (ds, eq)

def metrics_real(t, curve):
    if t is None or t.height == 0: return {"n": 0}
    r = t["ret"].to_numpy(); w = r[r > 0].sum(); l = -r[r < 0].sum()
    ds, eq = curve
    mdd = (eq / np.maximum.accumulate(np.maximum(eq, 1)) - 1).min()
    return {"n": len(r), "win": round(100 * (r > 0).mean(), 1), "avg": round(100 * r.mean(), 2),
            "pf": round(w / l, 2) if l > 0 else 99, "days": round(float(t["days"].mean()), 1),
            "tot": round(100 * (eq[-1] - 1), 1), "mdd": round(100 * mdd, 1)}
