"""[中线] §272 日历回避 CAL_A/CAL_B + §273 财务过滤 FIN_A/FIN_B（预注册见 docs/strategy-research.md §272/§273，结果出来前写定，不调参）。
基线 = mid-small-cap-weekly live（同 lt_research11：周五、小盘等权指数>MA20、U 池且 pe>0 折算市值最小 10 只，持 20 日，K=20，max_new=10）。
用法：cd backend/scripts/midterm && PYTHONPATH=../..:. ../../.venv/bin/python xbx_ab_s272.py ../../output/research/s272/res.json"""
import sys, json, glob, os
import numpy as np
import polars as pl
from lt_research import build, fixed, PH
from realistic import run_real, metrics_real
import lt_data as L
c = pl.col
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
OUT = sys.argv[1]
NSEED = 20
CRASH = ("2024-01-01", "2024-02-29")

df = build()
px = df.select("code", c("trade_date").alias("bd"), c("ac").alias("ac_bd"))
df = df.join(px, on=["code", "bd"], how="left").with_columns(
    (c("total_mv").cast(pl.Float64, strict=False) * c("ac") / c("ac_bd")).alias("mvd"))
g = (c("mvd").is_not_null() & (c("pe_ttm") > 0) & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"]))
r = df.filter(g).with_columns(c("mvd").rank("ordinal").over("trade_date").alias("r4"))
idx = (r.filter(c("r4") <= 400).group_by("trade_date").agg(c("pct_chg").clip(-20, 20).mean().alias("s")).sort("trade_date")
       .with_columns((1 + c("s") / 100).cum_prod().alias("scid")).with_columns(c("scid").rolling_mean(20).alias("scid20"))
       .select("trade_date", "scid", "scid20"))
del r
df = df.join(idx, on="trade_date", how="left")
# ---- PIT 财务（公告日严格早于信号日）
cols = ["ts_code", "ann_date", "end_date", "interestdebt", "netdebt", "ocf_to_shortdebt", "tangible_asset", "tbassets_to_totalassets"]
parts = []
for f in glob.glob(str(L.D / "fina" / "*.parquet")):
    try: parts.append(pl.read_parquet(f, columns=cols))
    except Exception: pass
fn = pl.concat(parts, how="vertical_relaxed")
num = cols[3:]
fn = fn.with_columns(*[c(k).cast(pl.Float64, strict=False) for k in num], c("ann_date").cast(pl.Utf8), c("end_date").cast(pl.Utf8))
fn = fn.filter(c("ann_date").str.len_chars() == 8).with_columns(
    c("ts_code").str.slice(0, 6).alias("code"),
    (c("ann_date").str.slice(0, 4) + "-" + c("ann_date").str.slice(4, 2) + "-" + c("ann_date").str.slice(6, 2)).alias("fd"),
    (c("interestdebt") / (c("tangible_asset") / (c("tbassets_to_totalassets") / 100))).alias("idr"))
fn = (fn.sort("end_date").unique(["code", "fd"], keep="last").sort("fd")
        .select("code", "fd", "idr", "netdebt", "ocf_to_shortdebt"))
df = df.sort("trade_date").join_asof(fn, left_on="trade_date", right_on="fd", by="code", strategy="backward",
                                      allow_exact_matches=False).sort("code", "trade_date")
wk = (c("trade_date").str.to_date().dt.weekday() == 5)
U = ((c("amt20") > 3e7) & (c("age") > 250) & ~c("st") & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
     & c("mvd").is_not_null()).fill_null(False)
uni = U & (c("pe_ttm") > 0)
on = c("scid") > c("scid20")
base = df.select("code", "trade_date", "ao", "ac", "lim_up_open", "lock_down", "mvd").with_columns((-c("mvd")).fill_null(0).alias("sc"))
# ---- 只在周五子表上做选股
td = df.select("trade_date").unique().sort("trade_date").with_columns(c("trade_date").shift(-1).alias("buy"))
fr = (df.filter(wk.fill_null(False) & on.fill_null(False) & uni)
        .select("code", "trade_date", "mvd", "idr", "netdebt", "ocf_to_shortdebt")
        .join(td, on="trade_date", how="left").with_columns(c("buy").fill_null(c("trade_date"))))
del df
bm = c("buy").str.slice(5, 2).cast(pl.Int32); bd_ = c("buy").str.slice(8, 2).cast(pl.Int32)
fr = fr.with_columns(
    ((bm.is_in([1, 4, 12]))).alias("winA"),
    ((bm.is_in([1, 12])) | (bm.is_in([4, 9]) & (bd_ >= 16))).alias("winB"))
fr = fr.with_columns(pl.len().over("trade_date").alias("npool"))
def smallest10(f, flag=None):
    d = f if flag is None else f.filter(flag)
    d = d.with_columns(c("mvd").rank("ordinal").over("trade_date").alias("_r")).filter(c("_r") <= 10)
    return d.select("code", "trade_date")
live = smallest10(fr)
# FIN
nid = pl.when(c("idr").is_not_null()).then(1).otherwise(0).sum().over("trade_date")
ridr = pl.when(c("idr").is_not_null()).then(c("idr")).rank("ordinal", descending=True).over("trade_date")
fr = fr.with_columns((ridr <= (nid * 0.2).ceil()).fill_null(False).alias("dropA"),
                     ((c("netdebt") > 0) & (c("ocf_to_shortdebt") < 0)).fill_null(False).alias("dropB"))
finA, finB = smallest10(fr, ~c("dropA")), smallest10(fr, ~c("dropB"))
remA = live.join(fr.filter(c("dropA")).select("code", "trade_date"), on=["code", "trade_date"], how="inner")
remB = live.join(fr.filter(c("dropB")).select("code", "trade_date"), on=["code", "trade_date"], how="inner")
lwk = live.select("trade_date").unique()
frw = fr.join(lwk.with_columns(pl.lit(True).alias("islive")), on="trade_date", how="inner").select("trade_date", "winA", "winB").unique("trade_date")
calA = live.join(frw.filter(~c("winA")).select("trade_date"), on="trade_date", how="inner")
calB = live.join(frw.filter(~c("winB")).select("trade_date"), on="trade_date", how="inner")
skA = live.join(frw.filter(c("winA")).select("trade_date"), on="trade_date", how="inner")
skB = live.join(frw.filter(c("winB")).select("trade_date"), on="trade_date", how="inner")
nlw = lwk.height
pA, pB = 1 - calA["trade_date"].n_unique() / nlw, 1 - calB["trade_date"].n_unique() / nlw
print(f"live 信号周 {nlw}；CAL_A 跳过占比 {pA:.3f}（保留 {calA['trade_date'].n_unique()} 周）；CAL_B 跳过占比 {pB:.3f}（保留 {calB['trade_date'].n_unique()} 周）", flush=True)
print("FIN_A 平均每周剔除只数", fr.filter(c("dropA")).group_by("trade_date").len()["len"].mean(),
      "FIN_B", fr.filter(c("dropB")).group_by("trade_date").len()["len"].mean(),
      "live 前10被A剔除", remA.height, "被B剔除", remB.height, "总 live 选股", live.height, flush=True)
# ---- 随机对照
def hr(seed): return pl.concat_str([c("code"), c("trade_date"), pl.lit(str(seed))]).hash(seed + 7).cast(pl.Float64)
def wk_hr(seed): return pl.concat_str([c("trade_date"), pl.lit(f"cal{seed}")]).hash(seed + 11).cast(pl.Float64) / 1.8446744073709552e19
ctrl = {}
for s in range(NSEED):
    for nm, p in (("A", pA), ("B", pB)):
        w = live.with_columns(wk_hr(s).alias("_u")).filter(c("_u") >= p).select("code", "trade_date")
        ctrl[f"CAL_RAND_{nm}_{s}"] = w
    for nm, flag in (("A", "dropA"), ("B", "dropB")):
        k = fr.group_by("trade_date").agg(c(flag).sum().alias("k"))
        f2 = fr.join(k, on="trade_date").with_columns(hr(s).rank("ordinal").over("trade_date").alias("_hr"))
        f2 = f2.filter(c("_hr") > c("k"))
        ctrl[f"FIN_RAND_{nm}_{s}"] = smallest10(f2)
V = {"live": live, "CAL_A": calA, "CAL_B": calB, "skipped_A": skA, "skipped_B": skB, "FIN_A": finA, "FIN_B": finB,
     "removed_A": remA, "removed_B": remB}
V.update(ctrl)
print("选股表完成", {k: v.height for k, v in V.items() if not k.startswith(("CAL_RAND", "FIN_RAND"))}, flush=True)

def run_one(sig, s, e):
    codes = sig["code"].unique()
    b = base.filter(c("code").is_in(codes.implode()) & (c("trade_date") >= s)).join(
        sig.with_columns(pl.lit(True).alias("sig")), on=["code", "trade_date"], how="left").with_columns(c("sig").fill_null(False))
    t, cur = run_real(b, "sig", "sc", fixed(20), s, e, k_slots=20, max_new=10, max_hold=20)
    return t, cur

def extra(t, m):
    if t is None or t.height == 0: return m
    r = t["ret"].to_numpy()
    m["p5"] = round(100 * float(np.percentile(r, 5)), 2)
    streak = mx = 0
    for x in t.sort("entry")["ret"].to_list():
        streak = streak + 1 if x <= 0 else 0; mx = max(mx, streak)
    m["streak"] = mx
    te = t.filter(~c("entry").is_between(pl.lit(CRASH[0]), pl.lit(CRASH[1])))
    m["n_ex"] = te.height; m["avg_ex"] = round(100 * float(te["ret"].mean()), 2) if te.height else 0.0
    return m

res = {}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
for n, sig in V.items():
    isc = n.startswith(("CAL_RAND", "FIN_RAND"))
    for ph, (s, e) in PH.items():
        t, cur = run_one(sig, s, e)
        m = metrics_real(t, cur)
        m = extra(t, m) if not isc else m
        res.setdefault(n, {})[ph] = {a: float(v) for a, v in m.items()}
        if n in ("live", "CAL_A", "CAL_B", "FIN_A", "FIN_B") and ph in ("R12 2025-10~2026-10", "N3 2024-01~2026-10") and t is not None and t.height:
            t.write_csv(os.path.join(os.path.dirname(OUT), f"trades_{n}_{ph[:3].strip()}.csv"))
    if not isc:
        print("##", n, {ph: (int(v.get("n", 0)), v.get("avg"), v.get("pf")) for ph, v in res[n].items()}, flush=True)
    elif n.endswith("_0") or n.endswith("_19"):
        print("##", n, "ok", flush=True)
    json.dump(res, open(OUT, "w"), indent=1)
print("DONE", flush=True)
