"""[中线/长线] §152 低综合股本发行（Daniel–Titman 2006 composite issuance）月度组合 — 预注册 3 版
（/workspace/strategy-research.md §152，2026-10-19 18:58 CST 结果出来前写定，无网格、不调参）。
引擎同 lt_research35~43（lt_research16.run）：月末收盘信号、次日开盘等权买、固定持有、已持有不重复买、不递补；lt_data 窗口加载。
只用本地 parquet（daily_kbar 缓存 / daily_hist / 周度 daily_basic / st_hist / stk_limits），不调用任何外部数据。
特征（PIT）：ac = Π(1+clip(pct_chg,±35%)/100)；周度快照 s 上 lmv_s = ln(total_mv_s) − ln(ac_s)（同一天）。
  月末 t：s1 = t 之前最近快照（t−s1 ≤ 10 天），s0 = (s1−365天) 之前最近快照（差 ≤ 14 天）；ISS = lmv_s1 − lmv_s0；
  ISS < −0.5 或 > 2.0 视为数据异常剔除。
剔壳：同 §126。股池 U：同 §133 基础池 + 非壳 + ISS 非空 + 剔除信号日收盘跌停（不剔金融）。
  ISS_h20    : U 内 ISS 从低到高前 10（同值按代码），持 20（容量 20，权益 1/10）-> /mid
  ISS_h60    : 同选股，持 60（容量 40，权益 1/30）-> /long
  ISS_LV_h60 : U 内 ISS 升序名次 ≤ ceil(10%|U|) 且 vol60 ≤ U 中位数，交集内 ISS 从低到高前 10，持 60 -> /long
信息：与 §124 REV（U 内 20 日收益最低 10 只）、LTV、DVLV 月均重叠。
阶段：R12/N3/W1/W2/W0/W00/P21/P20/P19。
对照（判定）：cohort_bench（lt_research22.cohort）；随机同五分位 20 种子（仅报告）。
用法：LT_START=.. LT_END=2026-10-16 python lt_research44.py "<phase>,<phase>" out.json"""
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
from lt_research34 import snap, by_year
c = pl.col
PH = dict(PH); PH["N3 2023-10~2026-10"] = ("2023-10-01", "2026-10-16")
NSEED = 20
OUTDIR = Path(__file__).resolve().parents[2] / "output/research/sprint4"
C = {"ISS_h20": ("rk_i", 20, 20, 10, True), "ISS_h60": ("rk_i", 60, 40, 30, True),
     "ISS_LV_h60": ("rk_il", 60, 40, 30, True),
     "LTV_h60_ref": ("rk_ltv", 60, 40, 30, False), "DVLV_h60_ref": ("rk_dvlv", 60, 40, 30, False)}
SHELL = 0.30
ISS_LO, ISS_HI = -0.5, 2.0


def prep():
    s0 = os.environ.get("LT_START")
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
                         (c("ac").shift(20).over("code") / c("ac").shift(120).over("code") - 1).alias("r120x20"),
                         (c("ac") / c("ac").shift(20).over("code") - 1).alias("r20"),
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
    df = iss_feats(df, s0)
    cross = c("is_me") & ~c("st") & c("mvd").is_not_null()
    df = df.with_columns((pl.when(cross).then(c("mvd")).rank("ordinal").over("trade_date")
                          / pl.when(cross).then(1).sum().over("trade_date")).alias("mv_pct"))
    df = df.with_columns(pl.when(c("mvd").is_not_null()).then(-c("mvd")).rank("ordinal").over("trade_date").alias("rk_mv"))
    base = c("is_me") & ~c("st") & (c("close") >= 3) & (c("nxt") == c("mkt_nxt")) & c("vol60").is_not_null()
    uh = (base & (c("amt20") > 5e7) & (c("age") > 250) & (c("mv_pct") > SHELL) & c("iss").is_not_null() & ~c("close_dn")).fill_null(False)
    ur = (base & c("rk_mv").is_between(1, 1500) & (c("age") >= 250) & c("turn60").is_not_null()).fill_null(False)
    df = df.with_columns(uh.alias("uh"), ur.alias("ur"))
    u = scores(df.filter(c("uh") | c("ur")))
    liqd = df.filter((c("amt20") > 5e7) & (c("age") > 250) & ~c("st")).select("code", "trade_date", "mvd")
    px = df.select("code", "trade_date", "ac", "ao", "lim_up_open", "lock_down").sort("code", "trade_date")
    return px, u, td.drop("mi"), liqd


def iss_feats(df, s0):
    """ISS（见模块说明）：周度快照日上 lmv = ln(total_mv) − ln(ac)（同一天），月末 t 取 s1 / s0 两个快照之差。T 日收盘可得。"""
    S = snap(s0, None).select("code", c("trade_date").str.to_date().alias("sd"), "total_mv").filter(c("total_mv") > 0)
    A = df.select("code", c("trade_date").str.to_date().alias("sd"), "ac")
    S = (S.join(A, on=["code", "sd"], how="inner").with_columns((c("total_mv").log() - c("ac").log()).alias("lmv"))
          .select("code", "sd", "lmv").filter(c("lmv").is_finite()).sort("sd"))
    me = df.filter(c("is_me")).select("code", "trade_date").with_columns(c("trade_date").str.to_date().alias("t")).sort("t")
    m1 = me.join_asof(S.rename({"sd": "sd1", "lmv": "lmv1"}), left_on="t", right_on="sd1", by="code", strategy="backward")
    m1 = m1.filter(c("sd1").is_not_null()).with_columns((c("sd1") - pl.duration(days=365)).alias("tgt")).sort("tgt")
    m2 = m1.join_asof(S.rename({"sd": "sd0", "lmv": "lmv0"}), left_on="tgt", right_on="sd0", by="code", strategy="backward")
    m2 = m2.with_columns((c("t") - c("sd1")).dt.total_days().alias("g1"), (c("tgt") - c("sd0")).dt.total_days().alias("g0"))
    ok = m2.filter(c("sd0").is_not_null() & (c("g1") <= 10) & (c("g0") <= 14)).with_columns((c("lmv1") - c("lmv0")).alias("iss_raw"))
    bad = ok.filter((c("iss_raw") < ISS_LO) | (c("iss_raw") > ISS_HI))
    ok = ok.with_columns(pl.when((c("iss_raw") >= ISS_LO) & (c("iss_raw") <= ISS_HI)).then(c("iss_raw")).alias("iss"))
    print("iss month-end rows", me.height, "valid", ok.height, "data-error excluded", bad.height,
          "(lo", bad.filter(c("iss_raw") < ISS_LO).height, "hi", bad.filter(c("iss_raw") > ISS_HI).height, ")",
          "iss med", round(float(ok["iss"].median() or 0), 4), "p05", round(float(ok["iss"].quantile(0.05) or 0), 4), flush=True)
    df = df.join(ok.select("code", "trade_date", "iss"), on=["code", "trade_date"], how="left").sort("code", "trade_date")
    return df


def _rank_desc(u, cond, col, name):
    """高值排名靠前（rank 1 = 最大）。"""
    s = (u.filter(cond & c(col).is_not_null()).sort(["trade_date", col, "code"], descending=[False, True, False])
         .with_columns((pl.int_range(pl.len()).over("trade_date") + 1).alias(name)).select("code", "trade_date", name))
    return u.join(s, on=["code", "trade_date"], how="left")


def _rank_asc(u, cond, col, name):
    """低值排名靠前（rank 1 = 最小），同值按代码。"""
    s = (u.filter(cond & c(col).is_not_null()).sort(["trade_date", col, "code"])
         .with_columns((pl.int_range(pl.len()).over("trade_date") + 1).alias(name)).select("code", "trade_date", name))
    return u.join(s, on=["code", "trade_date"], how="left")


def scores(u):
    u = u.select("code", "trade_date", "uh", "ur", "rk_mv", "mvd", "turn60", "vol60", "dv_ttm", "iss", "r20",
                 "r120x20", "mv_pct", "close").sort("trade_date", "code")
    u = _rank_asc(u, c("uh"), "iss", "rk_i")
    nu = pl.when(c("uh")).then(1).sum().over("trade_date")
    vmed = pl.when(c("uh")).then(c("vol60")).median().over("trade_date")
    u = u.with_columns(nu.alias("_nu"), vmed.alias("_vmed"))
    u = _rank_asc(u, c("uh") & (c("rk_i") <= (c("_nu") * 0.1).ceil()) & (c("vol60") <= c("_vmed")), "iss", "rk_il")
    u = u.drop("_nu", "_vmed")
    u = _rank_asc(u, c("uh"), "r20", "rk_rev")
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
        dvm = lambda f: round(float(f["dv_ttm"].fill_null(0).mean() or 0), 3)
        print(ph, "signal months", len(sig_dates), "U months", uu["trade_date"].n_unique(), "U/month", round(uu.height / nm_),
              "iss picks/month", round(pr("rk_i").height / nm_, 1), "isslv picks/month", round(pr("rk_il").height / nm_, 1),
              "iss pick mean", round(float(pr("rk_i")["iss"].mean() or 0), 4), "U iss med", round(float(uu["iss"].median() or 0), 4),
              "isslv pick mean", round(float(pr("rk_il")["iss"].mean() or 0), 4),
              "iss pick dv_ttm mean", dvm(pr("rk_i")), "isslv dv", dvm(pr("rk_il")), "U dv_ttm mean", dvm(uu),
              "iss pick r20 mean", round(float(pr("rk_i")["r20"].mean() or 0), 4), "U r20 med", round(float(uu["r20"].median() or 0), 4),
              "iss pick vol60 mean", round(float(pr("rk_i")["vol60"].mean() or 0), 4), "U vol60 med", round(float(uu["vol60"].median() or 0), 4),
              "iss pick mv_pct mean", round(float(pr("rk_i")["mv_pct"].mean() or 0), 3), "isslv mv_pct", round(float(pr("rk_il")["mv_pct"].mean() or 0), 3), flush=True)
        sets = {nm: {d: set(g["code"].to_list()) for (d,), g in us.filter(c(col) <= 10).group_by(["trade_date"])}
                for nm, col in (("LTV", "rk_ltv"), ("DVLV", "rk_dvlv"), ("REV", "rk_rev"))}
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
                if nm == "ISS_h20" and ph.split()[0] in ("R12", "N3"):
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
