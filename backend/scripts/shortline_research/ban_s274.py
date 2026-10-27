"""[短线研究 §274] 炸板阳线 BAN_1/2/3（预注册 docs/strategy-research.md §274，结果出来前写定，不调参）。
行情 = midterm 长面板 + realistic 引擎；只用本地日K与涨停价表。
用法：cd backend/scripts/midterm && PYTHONPATH=../..:. ../../.venv/bin/python ../shortline_research/ban_s274.py ../../output/research/s274/res.json"""
import sys, json, os
import numpy as np
import polars as pl
import lt_data as L
from realistic import add_limits, run_real, metrics_real
from lt_research import PH, fixed

c = pl.col
PHASES = dict(PH); PHASES["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
START, END = "2019-01-01", "2026-10-16"
K, MAX_NEW, HOLD, NSEED = 20, 5, 2, 10
OUT = sys.argv[1]

df = L.prices(START, END)
df = df.with_columns(((1 + c("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code")).alias("ac"),
                     pl.int_range(pl.len()).over("code").alias("age"))
df = df.with_columns((c("open") * c("ac") / c("close")).alias("ao"), c("close").shift(1).over("code").alias("pre_c"))
st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
df = df.sort("trade_date").join_asof(st, on="trade_date", by="code", strategy="backward").with_columns(c("st").fill_null(False))
df = add_limits(df.sort("code", "trade_date"))
b20 = c("code").str.starts_with("30") | c("code").str.starts_with("688")
band = pl.when(b20).then(0.198).otherwise(0.098)
up = pl.coalesce(c("up_limit"), c("pre_c") * (1 + (band + 0.002)))
touched = (c("high") >= up - 0.005).fill_null(False)
sealed = (c("close") >= up - 0.005).fill_null(False)
pct = c("close") / c("pre_c") - 1
main = c("code").str.starts_with("60") | c("code").str.starts_with("00")
uni = (~c("st") & (c("age") >= 250) & (c("amount") >= 1e8) & (c("pre_c") > 0) & (c("open") > 0)).fill_null(False)
ev = touched & ~sealed & uni
mid = (c("high") + c("low")) / 2
df = df.with_columns(
    (ev & main).alias("B1"), (ev & main & (c("close") >= c("open"))).alias("B2"),
    (ev & main & (c("close") >= c("open")) & (c("close") >= mid)).alias("B3"),
    (ev & b20).alias("B1_20"), (ev & b20 & (c("close") >= c("open"))).alias("B2_20"),
    (ev & b20 & (c("close") >= c("open")) & (c("close") >= mid)).alias("B3_20"),
    (uni & ~touched & main & pct.is_between(0.07, 0.099)).alias("CM"),
    (uni & ~touched & b20 & pct.is_between(0.14, 0.198)).alias("C20"),
    c("amount").alias("sc_amt"))
base = df.select("code", "trade_date", "ao", "ac", "lim_up_open", "lock_down", "B1", "B2", "B3", "B1_20", "B2_20", "B3_20", "CM", "C20", "sc_amt")
del df
print("事件日×股数", {k: int(base[k].sum()) for k in ("B1", "B2", "B3", "B1_20", "B2_20", "B3_20", "CM", "C20")}, flush=True)
for k in ("B1", "B2", "B3", "B1_20", "B2_20", "B3_20", "CM", "C20"):
    pd_ = base.filter(c(k)).group_by("trade_date").len()["len"]
    print(f"  {k}: 日均 {pd_.mean():.1f} 只, 有事件的日数 {pd_.len()}", flush=True)

def top5(flag, score):
    d = base.filter(c(flag)).with_columns(score.alias("_s"))
    d = d.with_columns(c("_s").rank("ordinal", descending=True).over("trade_date").alias("_r")).filter(c("_r") <= MAX_NEW)
    return d.select("code", "trade_date", "_s")

def run_sig(sig, s, e):
    codes = sig["code"].unique()
    b = base.filter(c("code").is_in(codes.implode()) & (c("trade_date") >= s)).select("code", "trade_date", "ao", "ac", "lim_up_open", "lock_down").join(
        sig.with_columns(pl.lit(True).alias("sig")), on=["code", "trade_date"], how="left").with_columns(c("sig").fill_null(False), c("_s").fill_null(0.0))
    t, cur = run_real(b, "sig", "_s", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
    return t, cur

def extra(t, m):
    if t is None or t.height == 0: return m
    streak = mx = 0
    for x in t.sort("entry")["ret"].to_list():
        streak = streak + 1 if x <= 0 else 0; mx = max(mx, streak)
    m["streak"] = mx
    return m

def hsc(seed): return pl.concat_str([c("code"), c("trade_date"), pl.lit(str(seed))]).hash(seed + 3).cast(pl.Float64) / 1.8446744073709552e19
V = {n: top5(n, c("sc_amt")) for n in ("B1", "B2", "B3", "B1_20", "B2_20", "B3_20")}
for s in range(NSEED):
    V[f"CTL_M_{s}"] = top5("CM", hsc(s)); V[f"CTL_20_{s}"] = top5("C20", hsc(s))
res = {}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
for n, sig in V.items():
    isc = n.startswith("CTL")
    for ph, (s, e) in PHASES.items():
        t, cur = run_sig(sig, s, e)
        m = metrics_real(t, cur)
        if not isc: m = extra(t, m)
        res.setdefault(n, {})[ph] = {a: float(v) for a, v in m.items()}
    if not isc:
        print("##", n, {ph: (int(v.get("n", 0)), v.get("avg"), v.get("pf")) for ph, v in res[n].items()}, flush=True)
    elif n.endswith("_0") or n.endswith("_9"):
        print("##", n, {ph: (int(v.get("n", 0)), v.get("avg")) for ph, v in res[n].items()}, flush=True)
    json.dump(res, open(OUT, "w"), indent=1)
print("DONE", flush=True)
