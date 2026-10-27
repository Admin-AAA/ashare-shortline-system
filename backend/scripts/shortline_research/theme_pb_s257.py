"""[短线研究 §257] 题材龙头「回头」(PB) 与题材「次龙·未封板」(SD)（预注册 docs/strategy-research.md §257，结果出来前写定，不调参）。
题材 = 开盘啦 kpl_list；行情 = midterm 长面板 + realistic 引擎；复用 §156 的 load_kpl / 阶段 / 出场框架。
用法：cd backend/scripts/midterm && PYTHONPATH=../..:. ../../.venv/bin/python ../shortline_research/theme_pb_s257.py out.json"""
import sys, json, os
from pathlib import Path
import numpy as np
import polars as pl
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lt_data as L
from realistic import add_limits, run_real
import theme_leader_s156 as S

c = pl.col
PHASES = dict(S.PHASES)
P8 = S.P8
START, END = S.START, S.END
K, MAX_NEW = 15, 3
SEEDS = (1, 2, 3)


def panel():
    df = L.prices(START, END)
    df = df.with_columns(((1 + c("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code")).alias("ac"))
    kf = c("ac") / c("close")
    pc = c("close").shift(1).over("code")
    df = df.with_columns((c("open") * kf).alias("ao"), (c("low") * kf).alias("al"),
                         ((c("high") - c("close")) / pc >= 0.04).cast(pl.Int32).alias("chong"),
                         pl.int_range(pl.len()).over("code").alias("age"))
    df = df.with_columns(c("chong").rolling_sum(10).over("code").alias("chong10"))
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.sort("trade_date").join_asof(st, on="trade_date", by="code", strategy="backward").with_columns(c("st").fill_null(False))
    df = add_limits(df.sort("code", "trade_date"))
    return df.select("code", "trade_date", "close", "high", "low", "pct_chg", "ao", "ac", "al", "amount", "chong10", "age", "st",
                     "lim_up_open", "lock_down")


def sec(x):
    return c(x).str.slice(0, 2).cast(pl.Int32, strict=False) * 3600 + c(x).str.slice(3, 2).cast(pl.Int32, strict=False) * 60 \
        + c(x).str.slice(6, 2).cast(pl.Int32, strict=False)


def build(df, k):
    lu = k.select("code", "trade_date").with_columns(pl.lit(1).alias("is_lu"))
    df = df.join(lu, on=["code", "trade_date"], how="left").with_columns(c("is_lu").fill_null(0))
    df = df.with_columns(c("is_lu").shift(1).rolling_sum(120, min_samples=1).over("code").fill_null(0).alias("gx"))
    days = df.select("trade_date").unique().sort("trade_date").with_columns(
        c("trade_date").shift(-1).alias("nxt"), pl.int_range(pl.len()).alias("di"))
    dlist = days["trade_date"].to_list()
    # ---- 题材展开（同 §156）
    th = k.with_columns(c("theme").fill_null("").str.split("、").alias("th")).explode("th").with_columns(c("th").str.strip_chars())
    th = th.filter((c("th").str.len_chars() > 0) & ~pl.any_horizontal([c("th").str.contains(b, literal=True) for b in S.BAD]))
    th = th.join(df.select("code", "trade_date", "gx", "chong10", "age", "st", c("amount").alias("pamt")), on=["code", "trade_date"], how="left")
    th = th.with_columns(pl.coalesce("amount", "pamt").alias("amt"))
    heat = th.group_by("trade_date", "th").agg(pl.len().cast(pl.Int64).alias("n"), c("amt").sum().alias("tamt"))
    heat = heat.with_columns(pl.struct(n=-c("n"), a=-c("tamt")).rank("ordinal").over("trade_date").alias("hr"))
    hot = heat.filter((c("hr") <= S.TOP_THEMES) & (c("n") >= S.MIN_N))
    t = th.join(hot.select("trade_date", "th", c("n").alias("nhot")), on=["trade_date", "th"], how="inner")
    t = t.with_columns(c("lu_time").fill_null("99:99:99"), c("last_time").fill_null(""))
    t = t.with_columns(pl.struct(a=c("lu_time"), b=c("code")).rank("ordinal").over("trade_date", "th").alias("r_lu"),
                       (-c("amt").fill_null(0)).rank("min").over("trade_date", "th").alias("r_amt"),
                       (-c("gx").fill_null(0)).rank("min").over("trade_date", "th").alias("r_gx"))
    t = t.with_columns((c("r_lu") + c("r_amt") + c("r_gx")).alias("ident"))
    t = t.with_columns(pl.struct(a=c("ident"), b=c("lu_time"), d=c("code")).rank("ordinal").over("trade_date", "th").alias("r_id"))
    uni_c = (~c("st").fill_null(True)) & (c("age").fill_null(0) >= 60) & (c("amt").fill_null(0) >= 1e8) \
        & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
    one_seal = ((sec("last_time") - sec("lu_time")).abs() <= 60).fill_null(False)
    t = t.with_columns(uni_c.alias("uni"), one_seal.alias("oseal"))
    # ---- T 日（L 的下一个交易日）特征
    dn = days.select(c("trade_date").alias("trade_date"), c("nxt").alias("T"))
    thr = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(19.5).otherwise(9.7)
    Tf = df.select(c("code"), c("trade_date").alias("T"), c("is_lu").alias("lu_T"), c("pct_chg").alias("pct_T"),
                   c("close").alias("cl_T"), c("high").alias("hi_T"), c("low").alias("lo_T"), c("chong10").alias("ch_T"),
                   (c("pct_chg") >= thr).fill_null(False).alias("near_lu_T"))
    Tf = Tf.with_columns(((c("cl_T") - c("lo_T")) / (c("hi_T") - c("lo_T"))).alias("pos_T"))
    heatn = heat.select(c("trade_date").alias("T"), "th", c("n").alias("n_T"))

    def pb_candidates(leaders, need_theme):
        x = leaders.join(dn, on="trade_date", how="left").join(Tf, on=["code", "T"], how="inner")
        x = x.filter((c("lu_T") == 0) & ~c("near_lu_T") & (c("pct_T") >= -5.0))
        if need_theme:
            x = x.join(heatn, on=["T", "th"], how="left").filter(c("n_T").fill_null(0) >= 3)
        return x

    sig = {}  # name -> (frame code,T,score)
    # PB 龙头：L 日热点题材内 IDENT 前 2
    lead = t.filter(c("uni") & (c("r_id") <= 2) & c("nb").is_not_null())
    lead_lo = lead.filter(c("nb") <= 3)
    pb1 = pb_candidates(lead_lo, True)
    sig["PB1"] = pb1.group_by("code", "T").agg((-c("ident").cast(pl.Float64)).max().alias("sc"))
    pb2 = pb1.filter(c("oseal") & (c("ch_T").fill_null(99) <= 1) & (c("pos_T") >= 0.5))
    sig["PB2"] = pb2.group_by("code", "T").agg((-c("ident").cast(pl.Float64)).max().alias("sc"))
    # 阶段拆分（诊断）
    for nm, cond in (("PB2a", c("nb") == 1), ("PB2b", c("nb").is_between(2, 3))):
        sig[nm] = pb2.filter(cond).group_by("code", "T").agg((-c("ident").cast(pl.Float64)).max().alias("sc"))
    pb2c = pb_candidates(lead.filter(c("nb") >= 4), True).filter(c("oseal") & (c("ch_T").fill_null(99) <= 1) & (c("pos_T") >= 0.5))
    sig["PB2c"] = pb2c.group_by("code", "T").agg((-c("ident").cast(pl.Float64)).max().alias("sc"))
    # 对照 PB_FOLLOW：同热点题材 r_id>=4 的涨停股，条件①②③
    fol = t.filter(c("uni") & (c("r_id") >= 4) & c("nb").is_not_null() & (c("nb") <= 3))
    ctrl_pbf = pb_candidates(fol, True).group_by("code", "T").agg(pl.len().alias("x"))
    # 对照 PB_MKT：全市场 L 日涨停（不限题材），条件①②
    allk = k.join(df.select("code", "trade_date", "gx", "chong10", "age", "st", c("amount").alias("pamt")), on=["code", "trade_date"], how="left")
    allk = allk.with_columns(pl.coalesce("amount", "pamt").alias("amt"))
    allk = allk.filter((~c("st").fill_null(True)) & (c("age").fill_null(0) >= 60) & (c("amt").fill_null(0) >= 1e8)
                       & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"]) & c("nb").is_not_null() & (c("nb") <= 3))
    allk = allk.select("code", "trade_date", pl.lit("x").alias("th"))
    ctrl_pbm = pb_candidates(allk, False).group_by("code", "T").agg(pl.len().alias("x"))
    # ---- SD：T 日热点题材成员
    kth = th.select("code", "trade_date", "th").unique().join(days.select("trade_date", "di"), on="trade_date")
    kth = kth.with_columns(pl.int_ranges(c("di") + 1, c("di") + 21).alias("tj")).explode("tj").filter(c("tj") < len(dlist))
    kth = kth.with_columns(c("tj").map_elements(lambda i: dlist[i], return_dtype=pl.String).alias("T"))
    mem = kth.join(hot.select(c("trade_date").alias("T"), "th"), on=["T", "th"], how="inner").select("code", "T", "th").unique()
    del kth
    Tg = df.select("code", c("trade_date").alias("T"), c("is_lu").alias("lu_T"), c("pct_chg").alias("pct_T"), c("close").alias("cl_T"),
                   c("high").alias("hi_T"), c("amount").alias("amt_T"), c("gx").alias("gx_T"), c("chong10").alias("ch_T"),
                   c("age").alias("age_T"), c("st").alias("st_T"))
    m = mem.join(Tg, on=["code", "T"], how="inner")
    thr2 = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(19.5).otherwise(9.7)
    base = m.filter((~c("st_T").fill_null(True)) & (c("age_T").fill_null(0) >= 60) & (c("amt_T").fill_null(0) >= 1e8)
                    & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"]) & (c("lu_T") == 0)
                    & (c("pct_T") >= 5.0) & (c("pct_T") < thr2))
    sd1 = base.filter(c("cl_T") >= c("hi_T") * 0.985)
    sd1 = sd1.with_columns((-c("amt_T")).rank("ordinal").over("T", "th").alias("ra")).filter(c("ra") <= 2)
    sig["SD1"] = sd1.group_by("code", "T").agg(c("amt_T").max().alias("sc"))
    sd2 = sd1.filter((c("gx_T") >= 1) & (c("ch_T").fill_null(99) <= 1))
    sig["SD2"] = sd2.group_by("code", "T").agg(c("amt_T").max().alias("sc"))
    ctrl_sd = base.group_by("code", "T").agg(pl.len().alias("x"))
    # ---- 合并到面板
    out = df.select("code", "trade_date", "ao", "ac", "al", "lim_up_open", "lock_down").rename({})
    for nm, f in sig.items():
        f = f.rename({"T": "trade_date", "sc": f"sc_{nm}"}).with_columns(pl.lit(True).alias(f"sig_{nm}"))
        out = out.join(f, on=["code", "trade_date"], how="left").with_columns(c(f"sig_{nm}").fill_null(False))
    for nm, f in (("CTRL_PB_FOLLOW", ctrl_pbf), ("CTRL_PB_MKT", ctrl_pbm), ("CTRL_SD", ctrl_sd)):
        f = f.rename({"T": "trade_date"}).select("code", "trade_date").with_columns(pl.lit(True).alias(f"sig_{nm}"))
        out = out.join(f, on=["code", "trade_date"], how="left").with_columns(c(f"sig_{nm}").fill_null(False))
        for s in SEEDS:
            out = out.with_columns(pl.struct("code", "trade_date").hash(seed=s).cast(pl.Float64).alias(f"sc_{nm}_{s}"))
    return out.sort("code", "trade_date")


def ex5(a, e, i):
    return a["ac"][i] < a["al"][i - 1] or a["ac"][i] < a["ao"][e] * 0.95 or i - e >= 4


def split(t, curve):
    res = {}
    ds, eq = curve
    ds = np.array(ds)
    for p, (a, b) in PHASES.items():
        tt = t.filter(c("entry").is_between(pl.lit(a), pl.lit(b)))
        m = (ds >= a) & (ds <= b)
        if tt.height == 0 or m.sum() == 0:
            res[p] = {"n": tt.height}
            continue
        i0 = int(np.argmax(m))
        base = eq[i0 - 1] if i0 > 0 else 1.0
        seg = np.concatenate([[1.0], 1.0 + (eq[m] - base)])
        r = tt["ret"].to_numpy(); w = r[r > 0].sum(); l = -r[r < 0].sum()
        streak = mx = 0
        for x in tt.sort("entry")["ret"].to_list():
            streak = streak + 1 if x <= 0 else 0; mx = max(mx, streak)
        res[p] = {"n": len(r), "win": round(100 * (r > 0).mean(), 1), "avg": round(100 * r.mean(), 2),
                  "pf": round(w / l, 2) if l > 0 else 99, "days": round(float(tt["days"].mean()), 2),
                  "max_loss_streak": mx, "tot": round(100 * (seg[-1] - 1), 1),
                  "mdd": round(100 * (seg / np.maximum.accumulate(seg) - 1).min(), 1)}
    return res


def main(out_path):
    k = S.load_kpl()
    print("kpl rows", k.height, flush=True)
    df = panel()
    out = build(df, k)
    del df
    print("signals built", out.shape, flush=True)
    for col in [x for x in out.columns if x.startswith("sig_")]:
        print(col, "signal-days", out.filter(c(col)).height, flush=True)
    res = {}
    def run(name, sig, sc, ex, mh, key):
        t, curve = run_real(out, sig, sc, ex, START, END, k_slots=K, max_new=MAX_NEW, max_hold=mh)
        r = split(t, curve) if t is not None and t.height else {}
        print(key, " ".join(f"{p.split()[0]}:{r.get(p, {}).get('n', 0)}/{r.get(p, {}).get('avg', 0):+.2f}" for p in PHASES), flush=True)
        if t is not None and t.height:
            t.write_parquet(Path(out_path).with_name(f"trades_{key.replace('|', '_')}.parquet"))
        return r
    for nm in ("PB1", "PB2", "PB2a", "PB2b", "PB2c", "SD1", "SD2"):
        res[f"{nm}|main"] = run(nm, f"sig_{nm}", f"sc_{nm}", S.ex_main, 3, f"{nm}|main")
        if nm in ("PB1", "PB2", "SD1", "SD2"):
            res[f"{nm}|E5"] = run(nm, f"sig_{nm}", f"sc_{nm}", ex5, 5, f"{nm}|E5")
    for nm in ("CTRL_PB_FOLLOW", "CTRL_PB_MKT", "CTRL_SD"):
        seeds = [run(nm, f"sig_{nm}", f"sc_{nm}_{s}", S.ex_main, 3, f"{nm}|main|s{s}") for s in SEEDS]
        agg = {}
        for p in PHASES:
            rows = [r[p] for r in seeds if r.get(p, {}).get("avg") is not None]
            if rows:
                agg[p] = {kk: round(float(np.mean([x[kk] for x in rows])), 2) for kk in ("n", "win", "avg", "pf", "days", "tot", "mdd", "max_loss_streak")}
        res[f"{nm}|main"] = agg
        res[f"{nm}|seeds"] = seeds
    Path(out_path).write_text(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
