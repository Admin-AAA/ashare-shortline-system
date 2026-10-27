"""[中线/长线] §145 纯盈利能力 GP/A（Novy-Marx）月度组合 — 预注册 3 版
（/workspace/strategy-research.md §145，2026-10-19 16:19 CST）。
GPA_h20/GPA_h60/GPA_LV_h60；特征同 lt_research34.agtab 的 GPA；不剔金融；引擎同 lt_research34/40。
用法：LT_START=.. LT_END=.. python lt_research41.py "<phase>,.." out.json"""
import sys, json, os, datetime as dt
from collections import defaultdict
from pathlib import Path
import numpy as np
import polars as pl
import lt_data as L
from lt_research import PH
from realistic import add_limits, metrics_real
from lt_research16 import run, expectancy, arrays
from lt_research19 import streak, payoff
from lt_research22 import cohort, rb_signals, rb_overlap, hold_overlap
c = pl.col
PH = dict(PH); PH["N3 2023-10~2026-10"] = ("2023-10-01", "2026-10-16")
NSEED = 20
OUTDIR = Path(__file__).resolve().parents[2] / "output/research/sprint4"
EXTRA = 0  # lt_data 420-day warmup already covers the 250-day age filter
C = {"GPA_h20": ("rk_g", 20, 20, 10, True), "GPA_h60": ("rk_g", 60, 40, 30, True),
     "GPA_LV_h60": ("rk_gl", 60, 40, 30, True),
     "LTV_h60_ref": ("rk_ltv", 60, 40, 30, False), "DVLV_h60_ref": ("rk_dvlv", 60, 40, 30, False)}
SHELL = 0.30
FIN = {"银行", "证券", "保险", "多元金融"}
IND = json.load(open(Path(__file__).resolve().parents[2] / "data/industry_cache.json"))
FRESH = 270
# [diagnostic only, post-hoc, NOT adoption-eligible] AGMODE=annual: AG = tushare assets_yoy of the latest ANNUAL report
# (balance-sheet based, exact YoY for 1231 reports), FRESH 480d, GPA kept only where bps > 0 (TA proxy breaks for
# near-zero / negative equity, which dominated the pre-registered lowest-AG picks).
AGMODE = os.environ.get("AGMODE", "")
if AGMODE == "annual": FRESH = 480


def agtab():
    """PIT asset-growth table: one row per (code, availability date) with AG = TA/TA_prev_year - 1 and GPA = GP_ttm/TA."""
    cols = ["ts_code", "ann_date", "end_date", "bps", "assets_to_eqt", "gross_margin"]
    parts = []
    for f in sorted((L.D / "fina").glob("*.parquet")):
        if not set(cols) <= set(pl.read_parquet_schema(f)): continue
        x = pl.read_parquet(f, columns=cols)
        parts.append(x.select([c(k).cast(pl.Utf8) if k in ("ts_code", "ann_date", "end_date") else c(k).cast(pl.Float64, strict=False) for k in cols]))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date"])
    d = (d.sort(["ann_date", c("bps").is_null()]).unique(["ts_code", "end_date"], keep="first")
          .with_columns(c("ts_code").str.slice(0, 6).alias("code"), L._dash("end_date").alias("end_dash"), L._dash("ann_date").alias("ann")))
    fs = sorted(L.D.glob("basic_[0-9]*.parquet"))
    sh = pl.concat([pl.read_parquet(f, columns=["ts_code", "trade_date", "total_share"]).with_columns(c("trade_date").cast(pl.Utf8)) for f in fs], how="diagonal_relaxed")
    sh = sh.select(c("ts_code").str.slice(0, 6).alias("code"), L._dash("trade_date").alias("sd"), c("total_share").cast(pl.Float64)).drop_nulls().unique(["code", "sd"]).sort("sd")
    d = d.sort("end_dash").join_asof(sh, left_on="end_dash", right_on="sd", by="code", strategy="backward").drop("sd")
    first = sh.group_by("code").agg(c("total_share").sort_by("sd").first().alias("ts0"))
    d = d.join(first, on="code", how="left").with_columns(c("total_share").fill_null(c("ts0"))).drop("ts0")
    d = d.with_columns((c("bps") * c("assets_to_eqt") * c("total_share")).alias("ta"), (c("gross_margin") / 1e4).alias("gp"),
                       c("end_date").str.slice(0, 4).cast(pl.Int32).alias("yr"), c("end_date").str.slice(4, 4).alias("md"))
    k = d.select("code", "yr", "md", "ta", "gp", "ann")
    fy = k.filter(c("md") == "1231").select("code", (c("yr") + 1).alias("yr"), c("gp").alias("gp_fy"))
    py = k.select("code", (c("yr") + 1).alias("yr"), "md", c("ta").alias("ta_py"), c("gp").alias("gp_py"), c("ann").alias("ann_py"))
    d = d.join(fy, on=["code", "yr"], how="left").join(py, on=["code", "yr", "md"], how="left")
    d = d.filter((c("ta") > 0) & (c("ta_py") > 0)).with_columns((c("ta") / c("ta_py") - 1).alias("ag"),
        pl.max_horizontal("ann", "ann_py").alias("avail"))
    gpt = pl.when(c("md") == "1231").then(c("gp")).otherwise(c("gp") + c("gp_fy") - c("gp_py"))
    d = d.with_columns((gpt / c("ta")).alias("gpa"))
    if AGMODE == "annual":
        d = d.filter(c("md") == "1231").with_columns(pl.when(c("bps") > 0).then(c("gpa")).alias("gpa"))
        y = []
        for f in sorted((L.D / "fina").glob("*.parquet")):
            if "assets_yoy" not in pl.read_parquet_schema(f): continue
            y.append(pl.read_parquet(f, columns=["ts_code", "ann_date", "end_date", "assets_yoy"]).with_columns(
                c("ts_code").cast(pl.Utf8), c("ann_date").cast(pl.Utf8), c("end_date").cast(pl.Utf8), c("assets_yoy").cast(pl.Float64, strict=False)))
        y = (pl.concat(y, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "assets_yoy"]).filter(c("end_date").str.ends_with("1231"))
             .sort("ann_date").unique(["ts_code", "end_date"], keep="first")
             .select(c("ts_code").str.slice(0, 6).alias("code"), L._dash("end_date").alias("end_dash"), L._dash("ann_date").alias("avail"), (c("assets_yoy") / 100).alias("ag")))
        d = y.join(d.select("code", "end_dash", "ta", "gpa"), on=["code", "end_dash"], how="left")
    d = d.sort(["avail", "end_dash"]).unique(["code", "avail"], keep="last")
    return d.select("code", "avail", "end_dash", "ta", "ag", "gpa").sort("avail")


def snap(start=None, end=None):
    """weekly daily_basic snapshot: total_share/float_share (万股), dv_ttm, pe_ttm, total_mv (万元); same windowing as shares()."""
    start, end = L._range(start, end)
    fs = [f for f in sorted(L.D.glob("basic_[0-9]*.parquet")) if not start or f.stem[-4:] >= start[:4]]
    cols = ["total_share", "float_share", "dv_ttm", "pe_ttm", "total_mv"]
    b = pl.concat([pl.read_parquet(f, columns=["ts_code", "trade_date"] + cols)
                   .with_columns(c("trade_date").cast(pl.Utf8), *[c(x).cast(pl.Float64, strict=False) for x in cols])
                   for f in fs], how="diagonal_relaxed")
    b = b.filter(L._win("trade_date", start, end, dash=False))
    return b.select(c("ts_code").str.slice(0, 6).alias("code"), L._dash("trade_date").alias("trade_date"), *cols
                    ).unique(["code", "trade_date"]).sort("trade_date")


def _start():
    s = os.environ.get("LT_START")
    return (dt.date.fromisoformat(s) - dt.timedelta(days=EXTRA)).isoformat() if s else None


def prep():
    s0 = _start()
    df = L.prices(s0, None).filter(~c("code").str.slice(0, 1).is_in(["4", "8", "9"]))
    df = df.with_columns(((1 + c("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code")).alias("ac"),
                         (c("pct_chg") / 100).clip(-0.35, 0.35).alias("r"))
    df = df.with_columns((c("open") * c("ac") / c("close")).alias("ao"),
                         pl.int_range(pl.len()).over("code").alias("age"),
                         c("amount").rolling_mean(20).over("code").alias("amt20"))
    df = df.sort("trade_date").join_asof(snap(s0, None), on="trade_date", by="code", strategy="backward")
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.join_asof(st, on="trade_date", by="code", strategy="backward").with_columns(c("st").fill_null(False))
    df = df.with_columns((c("total_share") * c("close")).alias("mvd"),
                         (c("amount") / (c("float_share") * 1e4 * c("close"))).alias("tov")).sort("code", "trade_date")
    df = df.with_columns(c("tov").rolling_mean(60).over("code").alias("turn60"),
                         c("r").rolling_std(60).over("code").alias("vol60"),
                         c("trade_date").shift(-1).over("code").alias("nxt"))
    df = add_limits(df.drop("total_share", "float_share", "amount", "volume"))
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    dn = pl.coalesce(c("down_limit"), c("close").shift(1).over("code") * (1 - band))
    df = df.with_columns((c("close") <= dn + 0.005).fill_null(False).alias("close_dn"))
    td = df.select("trade_date").unique().sort("trade_date")
    td = td.with_columns(c("trade_date").shift(-1).alias("mkt_nxt"), c("trade_date").shift(1).alias("mkt_prv"),
                         c("trade_date").str.slice(0, 7).alias("ym"), pl.int_range(pl.len()).alias("mi"))
    me = td.group_by("ym").agg(c("trade_date").max().alias("me"))
    td = td.join(me, on="ym").with_columns((c("trade_date") == c("me")).alias("is_me")).select("trade_date", "mkt_nxt", "mkt_prv", "is_me", "mi").sort("trade_date")
    df = df.join(td.select("trade_date", "mkt_nxt", "is_me", "mi"), on="trade_date", how="left").sort("code", "trade_date")
    ag = agtab()
    mt = df.filter(c("is_me")).select("code", "trade_date").sort("trade_date")
    mt = mt.join_asof(ag, left_on="trade_date", right_on="avail", by="code", strategy="backward")
    mt = mt.with_columns((c("trade_date").str.to_date() - c("end_dash").str.to_date()).dt.total_days().alias("age_rep"))
    mt = mt.with_columns(pl.when(c("age_rep") <= FRESH).then(c("ag")).alias("ag"), pl.when(c("age_rep") <= FRESH).then(c("gpa")).alias("gpa"))
    df = df.join(mt.select("code", "trade_date", "ag", "gpa"), on=["code", "trade_date"], how="left")
    df = df.with_columns(c("code").replace_strict(IND, default="", return_dtype=pl.Utf8).is_in(list(FIN)).alias("fin"))
    cross = c("is_me") & ~c("st") & c("mvd").is_not_null()
    df = df.with_columns((pl.when(cross).then(c("mvd")).rank("ordinal").over("trade_date")
                          / pl.when(cross).then(1).sum().over("trade_date")).alias("mv_pct"))
    df = df.with_columns(pl.when(c("mvd").is_not_null()).then(-c("mvd")).rank("ordinal").over("trade_date").alias("rk_mv"))
    base = c("is_me") & ~c("st") & (c("close") >= 3) & (c("nxt") == c("mkt_nxt")) & c("vol60").is_not_null()
    uh = (base & (c("amt20") > 5e7) & (c("age") > 250) & (c("mv_pct") > SHELL) & c("gpa").is_not_null() & ~c("close_dn")).fill_null(False)
    ur = (base & c("rk_mv").is_between(1, 1500) & (c("age") >= 250) & c("turn60").is_not_null()).fill_null(False)
    df = df.with_columns(uh.alias("uh"), ur.alias("ur"))
    u = scores(df.filter(c("uh") | c("ur")))
    liqd = df.filter((c("amt20") > 5e7) & (c("age") > 250) & ~c("st")).select("code", "trade_date", "mvd")
    px = df.select("code", "trade_date", "ac", "ao", "lim_up_open", "lock_down").sort("code", "trade_date")
    return px, u, td.drop("mi"), liqd



def _rank_desc(u, cond, col, name):
    """高值排名靠前（rank 1 = 最大）。"""
    s = (u.filter(cond & c(col).is_not_null()).sort(["trade_date", col, "code"], descending=[False, True, False])
         .with_columns((pl.int_range(pl.len()).over("trade_date") + 1).alias(name)).select("code", "trade_date", name))
    return u.join(s, on=["code", "trade_date"], how="left")

def _rank_asc(u, cond, col, name):
    s = (u.filter(cond & c(col).is_not_null()).sort(["trade_date", col, "code"])
         .with_columns((pl.int_range(pl.len()).over("trade_date") + 1).alias(name)).select("code", "trade_date", name))
    return u.join(s, on=["code", "trade_date"], how="left")


def scores(u):
    u = u.select("code", "trade_date", "uh", "ur", "rk_mv", "mvd", "turn60", "vol60", "dv_ttm", "ag", "gpa", "mv_pct", "close").sort("trade_date", "code")
    u = _rank_desc(u, c("uh"), "gpa", "rk_g")
    nu = pl.when(c("uh")).then(1).sum().over("trade_date")
    vmed = pl.when(c("uh")).then(c("vol60")).median().over("trade_date")
    u = u.with_columns(nu.alias("nu"), vmed.alias("vmed"))
    u = _rank_desc(u, c("uh") & (c("rk_g") <= (c("nu") * 0.1).ceil()) & (c("vol60") <= c("vmed")), "gpa", "rk_gl")
    qr = pl.when(c("uh") & c("mvd").is_not_null()).then(c("mvd")).rank("ordinal").over("trade_date")
    nq = pl.when(c("uh") & c("mvd").is_not_null()).then(1).sum().over("trade_date")
    u = u.with_columns((qr * 5 / nq).ceil().clip(1, 5).cast(pl.Int32).alias("q"))
    lt = c("ur") & (c("rk_mv") >= 200)
    u = u.with_columns(pl.when(lt).then(c("turn60")).rank("average").over("trade_date").alias("rt"),
                       pl.when(lt).then(c("vol60")).rank("average").over("trade_date").alias("rv"),
                       pl.when(lt).then(c("vol60")).rank("ordinal").over("trade_date").alias("rvo"))
    u = u.with_columns(pl.when(lt).then(c("rt") + c("rv") + c("rvo") * 1e-6).rank("ordinal").over("trade_date").alias("rk_ltv"))
    dv = c("ur") & c("dv_ttm").is_not_null() & (c("dv_ttm") > 0)
    u = u.with_columns(pl.when(dv).then(-c("dv_ttm")).rank("average").over("trade_date").alias("rd"),
                       pl.when(dv).then(c("vol60")).rank("average").over("trade_date").alias("rv2"),
                       pl.when(dv).then(c("vol60")).rank("ordinal").over("trade_date").alias("rvo2"))
    u = u.with_columns(pl.when(dv).then(c("rd") + c("rv2") + c("rvo2") * 1e-6).rank("ordinal").over("trade_date").alias("rk_dvlv"))
    return u.drop("rt", "rv", "rvo", "rd", "rv2", "rvo2", "nu", "vmed")


def by_year(t):
    g = t.with_columns(c("entry").str.slice(0, 4).alias("y")).group_by("y").agg(
        pl.len().alias("n"), (100 * c("ret").mean()).round(2).alias("avg"), (100 * (c("ret") > 0).mean()).round(1).alias("win")).sort("y")
    return {r["y"]: [r["n"], r["avg"], r["win"]] for r in g.iter_rows(named=True)}


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    px, u, td, liqd = prep()
    rb = rb_signals(td, liqd)
    print("built", px.height, "uni rows", u.height, "rb signals", rb.height, flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    me_all = td.filter(c("is_me") & c("mkt_nxt").is_not_null())
    for ph in phases:
        s, e = PH[ph]
        sig_dates = me_all.filter((c("mkt_nxt") >= s) & (c("mkt_nxt") <= e))["trade_date"].to_list()
        codes, idx = arrays(px, sig_dates[0])
        us = u.filter(c("trade_date").is_in(sig_dates))
        pool = {(d, b): g["code"].to_list() for (d, b), g in us.filter(c("uh") & c("q").is_not_null()).sort("code").group_by(["trade_date", "q"])}
        uu = us.filter(c("uh")); nm_ = max(1, len(sig_dates))
        pr = lambda col: uu.filter(c(col) <= 10)
        print(ph, "signal months", len(sig_dates), "U months", uu["trade_date"].n_unique(), "U/month", round(uu.height / nm_),
              "gpa pick mean", round(float(pr("rk_g")["gpa"].mean() or 0), 4), "gpa pick med", round(float(pr("rk_g")["gpa"].median() or 0), 4),
              "glv pick gpa mean", round(float(pr("rk_gl")["gpa"].mean() or 0), 4), "glv pick vol60 mean", round(float(pr("rk_gl")["vol60"].mean() or 0), 4),
              "U ag med", round(float(uu["ag"].median() or 0), 4), "U gpa med", round(float(uu["gpa"].median() or 0), 4),
              "U gpa nonnull", round(float(uu["gpa"].is_not_null().mean() or 0), 3), "glv picks/month", round(pr("rk_gl").height / nm_, 1), flush=True)
        sets = {nm: {d: set(g["code"].to_list()) for (d,), g in us.filter(c(col) <= 10).group_by(["trade_date"])}
                for nm, col in (("LTV", "rk_ltv"), ("DVLV", "rk_dvlv"))}
        for nm, (col, hold, kc, kn, var) in C.items():
            pk = us.filter(c(col) <= 10).sort("trade_date", col)
            by = defaultdict(list); cnt = defaultdict(lambda: defaultdict(int))
            for code, d, q in pk.select("code", "trade_date", "q").iter_rows():
                by[d].append(code); cnt[d][q] += 1
            t, cur = run(codes, idx, by, sig_dates, hold, kc, kn)
            m = {a: float(v) for a, v in metrics_real(t, cur).items()}
            if t is not None:
                m["exp"] = expectancy(t); m["max_loss_streak"] = streak(t); m.update(payoff(t)); m["by_year"] = by_year(t)
            for rn, ss in sets.items():
                m[f"ovl_{rn}"] = round(float(np.mean([len(set(by.get(d, [])) & ss.get(d, set())) for d in sig_dates])), 2)
            m["mean_rk_mv"] = round(float(pk["rk_mv"].mean()), 0) if pk.height else None
            m["last_picks"] = {d: by[d] for d in sig_dates[-2:]}
            if var and t is not None:
                b = cohort(t, us, px, td)
                m["bench"] = round(100 * float(b["bench"].mean()), 2); m["excess"] = round(m["avg"] - m["bench"], 2)
                m["ex_win"] = round(100 * float(((b["ret"] - b["bench"]) > 0).mean()), 1)
                m["bench_cover"] = round(float(b["bench"].is_not_null().mean()), 3)
                m["ovl_RB_upper"] = rb_overlap(t, rb, td)
                if nm == "GPA_h20" and ph.split()[0] in ("R12", "N3"):
                    dd = OUTDIR / "datafix"; k = ph.split()[0]
                    for ref in ("EA_h20", "F_any_h20"):
                        m[f"hold_ovl_{ref}"] = hold_overlap(t, str(dd / f"lt20_{ref}_{k}_trades.csv"))
                    m["hold_ovl_BT_prem_h20"] = hold_overlap(t, str(OUTDIR / f"lt25_block_trade_BT_prem_h20_{k}_trades.csv"))
                cs = []
                for seed in range(NSEED):
                    byr = {}
                    for d in sig_dates:
                        rng = np.random.default_rng([seed, int(d.replace("-", ""))])
                        lst = []
                        for q, k in sorted((kk, vv) for kk, vv in cnt[d].items() if kk is not None):
                            p = pool.get((d, q), [])
                            if p: lst += [str(x) for x in rng.choice(p, size=min(k, len(p)), replace=False)]
                        rng.shuffle(lst); byr[d] = lst
                    tc, cc = run(codes, idx, byr, sig_dates, hold, kc, kn)
                    cs.append(metrics_real(tc, cc))
                ca = float(np.mean([float(x.get("avg", 0)) for x in cs]))
                m.update({"rnd_avg": round(ca, 2), "rnd_exc": round(m.get("avg", 0) - ca, 2)})
                if ph.split()[0] in ("R12", "N3"):
                    t.write_csv(str(Path(out).with_name(Path(out).stem + f"_{nm}_{ph.split()[0]}_trades.csv")))
            res.setdefault(nm, {})[ph] = m
            print(nm, ph, {k: v for k, v in m.items() if k != "last_picks"}, flush=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
        del codes, idx
    print("done", flush=True)
