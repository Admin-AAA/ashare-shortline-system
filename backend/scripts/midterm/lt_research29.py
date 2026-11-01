"""[中线/长线] §120 应计异象 / 现金流质量（Sloan 1996 思路）月度组合 — 预注册 3 版
（/workspace/strategy-research.md §120，结果出来前写定，无网格、不调参）。
引擎同 lt_research28（lt_research16.run）：月末收盘信号、次日开盘等权买、固定持有、已持有不重复买、不递补；lt_data 窗口加载。
只用本地 parquet（daily_kbar 缓存 / daily_hist / 周度 daily_basic / st_hist / fina_indicator 缓存 / industry_cache.json），不调用任何外部数据。
因子：每份报告（YTD 累计）换总额 NP = eps*总股本、OCF = ocfps*总股本、TA = bps*assets_to_eqt*总股本（总股本取 end_date 当日或之前最近快照，缺则最早一期）；
  TTM：12 月报 = 本期；否则 本期 + 上年年报 − 上年同期（三份都要有）。同一 (code, end_date) 只取 ann_date 最早一行。
  月末 t：ann_date <= t 最近一份报告且 end_date 距 t <= 270 天。ACC = (NP_ttm − OCF_ttm)/TA；CFY = OCF_ttm / 月末总市值。
股池 U：同 §118（liq 20日均额>5000万、面板内上市>250、非ST、非北交所；收盘>=3；次日有日K；vol60 非空）
  + 剔除 银行/证券/保险/多元金融 + ACC 可算 + NP_ttm > 0 + TA > 0。
  ACC_h20 : U 内 ACC 升序前 10（同值按代码），持 20（容量 20，权益 1/10）-> /mid
  ACC_h60 : 同选股，持 60（容量 40，权益 1/30）-> /long
  CFQ_h60 : U ∧ OCF_ttm >= NP_ttm 内 CFY 降序前 10，持 60（容量 40，权益 1/30）-> /long
对照（判定）：cohort_bench（lt_research22.cohort）；随机同五分位 20 种子（仅报告）。
参照：LTV_h60 / DVLV_h60 月度重叠、RB 上界、ACC_h20 对 EA_h20 / F_any_h20 / BT_prem_h20 持有期交集。
用法：LT_START=.. LT_END=.. python lt_research29.py "<phase>,<phase>" out.json"""
import sys, json, os, datetime as dt
from collections import defaultdict
from pathlib import Path
import numpy as np
import polars as pl
import lt_data as L
from lt_research import PH
from realistic import add_limits, metrics_real
from lt_research16 import run, expectancy, arrays
from lt_research19 import shares, streak, payoff
from lt_research22 import cohort, rb_signals, rb_overlap, hold_overlap
c = pl.col
PH = dict(PH); PH["N3 2023-10~2026-10"] = ("2023-10-01", "2026-10-16")
NSEED = 20
OUTDIR = Path(__file__).resolve().parents[2] / "output/research/sprint4"
EXTRA = 0  # lt_data 420-day warmup already covers the 250-day age filter
C = {"ACC_h20": ("rk_acc", 20, 20, 10, True), "ACC_h60": ("rk_acc", 60, 40, 30, True),
     "CFQ_h60": ("rk_cfq", 60, 40, 30, True),
     "LTV_h60_ref": ("rk_ltv", 60, 40, 30, False), "DVLV_h60_ref": ("rk_dvlv", 60, 40, 30, False)}
FIN = {"银行", "证券", "保险", "多元金融"}
IND = json.load(open(Path(__file__).resolve().parents[2] / "data/industry_cache.json"))
FRESH = 270


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
    df = df.sort("trade_date").join_asof(shares(s0, None), on="trade_date", by="code", strategy="backward")
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.join_asof(st, on="trade_date", by="code", strategy="backward").with_columns(c("st").fill_null(False))
    df = df.with_columns((c("total_share") * c("close")).alias("mvd"),
                         (c("amount") / (c("float_share") * 1e4 * c("close"))).alias("tov")).sort("code", "trade_date")
    df = df.with_columns(c("tov").rolling_mean(60).over("code").alias("turn60"),
                         c("r").rolling_std(60).over("code").alias("vol60"),
                         c("trade_date").shift(-1).over("code").alias("nxt"),
                         c("mvd").shift(1).over("code").alias("mvd_prev"))
    df = add_limits(df.drop("total_share", "float_share", "amount", "volume"))
    td = df.select("trade_date").unique().sort("trade_date")
    td = td.with_columns(c("trade_date").shift(-1).alias("mkt_nxt"), c("trade_date").shift(1).alias("mkt_prv"),
                         c("trade_date").str.slice(0, 7).alias("ym"))
    me = td.group_by("ym").agg(c("trade_date").max().alias("me"))
    td = td.join(me, on="ym").with_columns((c("trade_date") == c("me")).alias("is_me")).select("trade_date", "mkt_nxt", "mkt_prv", "is_me").sort("trade_date")
    df = df.join(td.select("trade_date", "mkt_nxt", "is_me"), on="trade_date", how="left")
    df = df.with_columns(pl.when(c("mvd").is_not_null()).then(-c("mvd")).rank("ordinal").over("trade_date").alias("rk_mv"))
    acc = accruals()
    me = df.filter(c("is_me")).select("code", "trade_date", "mvd").sort("trade_date")
    me = me.join_asof(acc, left_on="trade_date", right_on="ann", by="code", strategy="backward")
    me = me.with_columns((c("trade_date").str.to_date() - c("end_dash").str.to_date()).dt.total_days().alias("age_rep"))
    me = me.with_columns(pl.when(c("age_rep") <= FRESH).then(c("acc")).alias("acc"),
                         pl.when(c("age_rep") <= FRESH).then(c("ocf_t") / c("mvd")).alias("cfy"),
                         pl.when(c("age_rep") <= FRESH).then(c("ocf_t") >= c("np_t")).alias("cashok"))
    df = df.join(me.select("code", "trade_date", "acc", "cfy", "cashok"), on=["code", "trade_date"], how="left")
    fin = c("code").replace_strict(IND, default="", return_dtype=pl.Utf8).is_in(list(FIN))
    df = df.with_columns(fin.alias("fin"))
    base = c("is_me") & ~c("st") & (c("close") >= 3) & (c("nxt") == c("mkt_nxt")) & c("vol60").is_not_null()
    uh = (base & (c("amt20") > 5e7) & (c("age") > 250) & ~c("fin") & c("acc").is_not_null()).fill_null(False)
    ur = (base & c("rk_mv").is_between(1, 1500) & (c("age") >= 250) & c("turn60").is_not_null()).fill_null(False)
    df = df.with_columns(uh.alias("uh"), ur.alias("ur"))
    u = scores(df.filter(c("uh") | c("ur")))
    liqd = df.filter((c("amt20") > 5e7) & (c("age") > 250) & ~c("st")).select("code", "trade_date", "mvd")
    px = df.select("code", "trade_date", "ac", "ao", "lim_up_open", "lock_down").sort("code", "trade_date")
    return px, u, td, liqd


def accruals():
    """PIT accrual table: one row per (code, first ann_date of each end_date) with TTM NP / OCF and TA (万元)."""
    cols = ["ts_code", "ann_date", "end_date", "eps", "ocfps", "bps", "assets_to_eqt"]
    parts = []
    for f in sorted((L.D / "fina").glob("*.parquet")):
        sc = pl.read_parquet_schema(f)
        if not set(cols) <= set(sc): continue
        x = pl.read_parquet(f, columns=cols)
        parts.append(x.select([c(k).cast(pl.Utf8) if k in ("ts_code", "ann_date", "end_date") else c(k).cast(pl.Float64, strict=False) for k in cols]))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date"])
    d = (d.sort(["ann_date", c("eps").is_null()]).unique(["ts_code", "end_date"], keep="first")
          .with_columns(c("ts_code").str.slice(0, 6).alias("code"), L._dash("end_date").alias("end_dash"), L._dash("ann_date").alias("ann")))
    fs = sorted(L.D.glob("basic_[0-9]*.parquet"))
    sh = pl.concat([pl.read_parquet(f, columns=["ts_code", "trade_date", "total_share"]).with_columns(c("trade_date").cast(pl.Utf8)) for f in fs], how="diagonal_relaxed")
    sh = sh.select(c("ts_code").str.slice(0, 6).alias("code"), L._dash("trade_date").alias("sd"), c("total_share").cast(pl.Float64)).drop_nulls().unique(["code", "sd"]).sort("sd")
    d = d.sort("end_dash")
    d = d.join_asof(sh, left_on="end_dash", right_on="sd", by="code", strategy="backward").drop("sd")
    first = sh.sort("sd").group_by("code").agg(c("total_share").first().alias("ts0"))
    d = d.join(first, on="code", how="left").with_columns(c("total_share").fill_null(c("ts0"))).drop("ts0")
    d = d.with_columns((c("eps") * c("total_share")).alias("np"), (c("ocfps") * c("total_share")).alias("ocf"),
                       (c("bps") * c("assets_to_eqt") * c("total_share")).alias("ta"),
                       c("end_date").str.slice(0, 4).cast(pl.Int32).alias("yr"), c("end_date").str.slice(4, 4).alias("md"))
    k = d.select("code", "yr", "md", "np", "ocf")
    fy = k.filter(c("md") == "1231").select("code", (c("yr") + 1).alias("yr"), c("np").alias("np_fy"), c("ocf").alias("ocf_fy"))
    py = k.select("code", (c("yr") + 1).alias("yr"), "md", c("np").alias("np_py"), c("ocf").alias("ocf_py"))
    d = d.join(fy, on=["code", "yr"], how="left").join(py, on=["code", "yr", "md"], how="left")
    q4 = c("md") == "1231"
    d = d.with_columns(pl.when(q4).then(c("np")).otherwise(c("np") + c("np_fy") - c("np_py")).alias("np_t"),
                       pl.when(q4).then(c("ocf")).otherwise(c("ocf") + c("ocf_fy") - c("ocf_py")).alias("ocf_t"))
    d = d.filter(c("np_t").is_not_null() & c("ocf_t").is_not_null() & (c("ta") > 0) & (c("np_t") > 0))
    d = d.with_columns(((c("np_t") - c("ocf_t")) / c("ta")).alias("acc"))
    # several end_dates disclosed on the same day -> keep the latest end_date
    d = d.sort(["ann", "end_dash"]).unique(["code", "ann"], keep="last")
    return d.select("code", "ann", "end_dash", "np_t", "ocf_t", "ta", "acc").sort("ann")


def _rank_by(u, cond, col, name):
    s = (u.filter(cond & c(col).is_not_null()).sort(["trade_date", col, "code"], descending=[False, True, False])
         .with_columns((pl.int_range(pl.len()).over("trade_date") + 1).alias(name)).select("code", "trade_date", name))
    return u.join(s, on=["code", "trade_date"], how="left")


def scores(u):
    u = u.select("code", "trade_date", "uh", "ur", "rk_mv", "mvd", "turn60", "vol60", "dv_ttm", "acc", "cfy", "cashok", "close").sort("trade_date", "code")
    u = u.with_columns((-c("acc")).alias("nacc"))
    u = _rank_by(u, c("uh"), "nacc", "rk_acc")
    u = _rank_by(u, c("uh") & c("cashok").fill_null(False), "cfy", "rk_cfq")
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
    return u.drop("rt", "rv", "rvo", "rd", "rv2", "rvo2")


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
        nrm = us.filter(c("uh")).group_by("trade_date").len()
        ncq = us.filter(c("uh") & c("cashok").fill_null(False)).height
        print(ph, "signal months", len(sig_dates), "acc months", nrm.height, "U/month", round(us.filter(c("uh")).height / max(1, len(sig_dates))), "cashok/month", round(ncq / max(1, len(sig_dates))), flush=True)
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
                if nm == "ACC_h20" and ph.split()[0] in ("R12", "N3"):
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
