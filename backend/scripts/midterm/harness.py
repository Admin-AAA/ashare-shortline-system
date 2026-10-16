"""[中长线] multi-week holding backtest harness (research).

Adjusted prices are rebuilt from pct_chg (daily_kbars are unadjusted).
Signal at day t close -> entry at t+1 open; exit rule evaluated on closes ->
exit at next open. Cost 0.3% round trip. Portfolio: K equal slots.
"""
from __future__ import annotations
import numpy as np, polars as pl
from pathlib import Path

CACHE = Path(__file__).resolve().parents[2] / "data/cache/daily_kbar.parquet"
COST = 0.003
PHASES = {"W0 2022-04~2023-03": ("2022-04-01", "2023-03-31"),
          "W00 2023-04~2023-12": ("2023-04-01", "2023-12-31"),
          "W1 2024": ("2024-01-01", "2024-12-31"),
          "W2 2025-01~09": ("2025-01-01", "2025-09-30"),
          "R12 2025-10~2026-10": ("2025-10-01", "2026-10-13")}

def load() -> pl.DataFrame:
    df = (pl.scan_parquet(CACHE)
          .select("code", "trade_date", "open", "high", "low", "close", "amount",
                  "pct_chg", "turnover_rate", "macd_dif", "macd_dea", "volume")
          .filter((pl.col("close") > 0) & (pl.col("open") > 0))
          .sort("code", "trade_date").collect())
    df = df.with_columns(pl.col("trade_date").cast(pl.Utf8))
    g = pl.col("code")
    df = df.with_columns(
        ((1 + pl.col("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code")).alias("tri"))
    k = pl.col("tri") / pl.col("close")
    df = df.with_columns((pl.col("open") * k).alias("ao"), (pl.col("high") * k).alias("ah"),
                         (pl.col("low") * k).alias("al"), pl.col("tri").alias("ac"))
    c = pl.col("ac")
    df = df.with_columns(
        *[c.rolling_mean(n).over("code").alias(f"m{n}") for n in (5, 10, 20, 60, 120, 250)],
        pl.col("volume").rolling_mean(5).over("code").alias("v5"),
        pl.col("volume").rolling_mean(10).over("code").alias("v10"),
        pl.col("volume").rolling_mean(60).over("code").alias("v60"),
        pl.col("turnover_rate").rolling_mean(60).over("code").alias("t60"),
        pl.col("amount").rolling_mean(20).over("code").alias("amt20"),
        pl.col("ac").rolling_max(60).over("code").alias("hi60"),
        pl.col("ac").rolling_min(60).over("code").alias("lo60"),
        pl.col("ac").rolling_max(250).over("code").alias("hi250"),
        (c / c.shift(20).over("code") - 1).alias("r20"),
        (c / c.shift(60).over("code") - 1).alias("r60"),
        (c.shift(20).over("code") / c.shift(250).over("code") - 1).alias("r12_1"),
        (pl.col("pct_chg") / 100).rolling_std(60).over("code").alias("vol60"),
        pl.int_range(pl.len()).over("code").alias("age"),
    )
    return df

def run(df: pl.DataFrame, sig_col: str, score_col: str, exit_fn, start: str, end: str,
        k_slots: int = 10, max_new: int = 3, max_hold: int = 60, min_gap: int = 0):
    """Event backtest. exit_fn(arrays, i_entry, i) -> bool evaluated at close i."""
    dates = sorted(df.filter((pl.col("trade_date") >= start) & (pl.col("trade_date") <= end))
                   ["trade_date"].unique().to_list())
    if not dates: return None
    sub = df.filter(pl.col("trade_date") >= start)
    # per code arrays
    codes = {}
    for (code,), part in sub.partition_by("code", as_dict=True, maintain_order=True).items():
        codes[code] = {c: part[c].to_numpy() for c in part.columns if c not in ("code",)}
    cand = (sub.filter(pl.col(sig_col) & (pl.col("trade_date") <= end))
            .select("code", "trade_date", score_col).sort(["trade_date", score_col], descending=[False, True]))
    by_date = {}
    for code, d, s in cand.iter_rows():
        by_date.setdefault(d, []).append(code)
    idx = {c: {d: i for i, d in enumerate(a["trade_date"])} for c, a in codes.items()}
    open_pos = {}  # code -> exit date
    trades = []
    for d in dates:
        open_pos = {c: x for c, x in open_pos.items() if x > d}
        picks = [c for c in by_date.get(d, []) if c not in open_pos][:max_new]
        for c in picks:
            if len(open_pos) >= k_slots: break
            a = codes[c]; i0 = idx[c][d]
            if i0 + 1 >= len(a["trade_date"]): continue
            e = i0 + 1
            # skip one-word limit-up open (unbuyable)
            if a["ah"][e] == a["al"][e] and a["pct_chg"][e] > 9: continue
            px = a["ao"][e]; xi = None
            for i in range(e, min(len(a["trade_date"]) - 1, e + max_hold)):
                if exit_fn(a, e, i):
                    xi = i + 1; break
            if xi is None:
                xi = min(len(a["trade_date"]) - 1, e + max_hold)
            ret = a["ao"][xi] / px - 1 - COST if xi < len(a["trade_date"]) else np.nan
            trades.append((c, a["trade_date"][e], a["trade_date"][xi], xi - e, ret))
            open_pos[c] = a["trade_date"][xi]
    return pl.DataFrame(trades, schema=["code", "entry", "exit", "days", "ret"], orient="row")

def metrics(t: pl.DataFrame, k_slots: int = 10) -> dict:
    if t is None or t.height == 0: return {"n": 0}
    r = t["ret"].drop_nans().to_numpy()
    w = r[r > 0].sum(); l = -r[r < 0].sum()
    # equity: each trade contributes ret/k_slots at its exit date (sequential compounding approx)
    s = t.sort("exit").group_by("exit", maintain_order=True).agg(pl.col("ret").sum())
    eq = np.cumprod(1 + s["ret"].to_numpy() / k_slots)
    mdd = (eq / np.maximum.accumulate(eq) - 1).min() if len(eq) else 0
    return {"n": len(r), "win": round(100 * (r > 0).mean(), 1), "avg": round(100 * r.mean(), 2),
            "pf": round(w / l, 2) if l > 0 else 99, "days": round(t["days"].mean(), 1),
            "tot": round(100 * (eq[-1] - 1), 1) if len(eq) else 0, "mdd": round(100 * mdd, 1)}
