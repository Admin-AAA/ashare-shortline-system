"""[中线/长线] §118 残差动量（residual / idiosyncratic momentum，Blitz-Huij-Martens 2011 思路）月度组合 — 预注册 3 版
（/workspace/strategy-research.md §118，结果出来前写定，无网格、不调参）。
引擎同 lt_research22（lt_research16.run）：月末收盘信号、次日开盘等权买、固定持有、已持有不重复买、不递补；lt_data 窗口加载（预热 ~520 天）。
只用本地 parquet（daily_kbar 缓存 / daily_hist / 周度 daily_basic / st_hist），不调用任何外部数据。
因子：r = pct_chg/100（截尾 ±35%）；MKT_d = 全部非北交所 r 等权；SMB_d = 前一日折算总市值最小 30% 等权 r − 最大 30% 等权 r。
残差动量：月末 t，[t-249, t] 250 个市场交易日 OLS r = a + b1*MKT + b2*SMB + e（有效日 >= 200）；
  形成期 [t-249, t-21]（有效日 >= 150）：RM = sum(e)；RM_LV 分 = RM / std(e, ddof=1)。
股池 U：同 §108（liq 20日均额>5000万、面板内上市>250、非ST、非北交所；收盘>=3；次日有日K；vol60/RM 非空）。
  RM_h20   : U 内 RM 前 10（同值按代码），持 20（容量 20，权益 1/10）-> /mid
  RM_h60   : 同选股，持 60（容量 40，权益 1/30）-> /long
  RM_LV_h60: U 内 RM/σ_e 前 10，持 60（容量 40，权益 1/30）-> /long
对照（判定）：cohort_bench（lt_research22.cohort）；随机同五分位 20 种子（仅报告）。
参照：LTV_h60 / DVLV_h60 月度重叠、RB 上界、RM_h20 对 EA_h20 / F_any_h20 / BT_prem_h20 持有期交集。
用法：LT_START=.. LT_END=.. python lt_research28.py "<phase>,<phase>" out.json"""
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
EXTRA = 100  # extra calendar days on top of lt_data's 420-day warmup (250-day regression + 250-day age)
C = {"RM_h20": ("rk_rm", 20, 20, 10, True), "RM_h60": ("rk_rm", 60, 40, 30, True),
     "RM_LV_h60": ("rk_rmlv", 60, 40, 30, True),
     "LTV_h60_ref": ("rk_ltv", 60, 40, 30, False), "DVLV_h60_ref": ("rk_dvlv", 60, 40, 30, False)}
REG, SKIP, MIN_REG, MIN_FORM = 250, 21, 200, 150


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
    rm = resid_mom(df, td)
    df = df.join(rm, on=["code", "trade_date"], how="left")
    base = c("is_me") & ~c("st") & (c("close") >= 3) & (c("nxt") == c("mkt_nxt")) & c("vol60").is_not_null()
    uh = (base & (c("amt20") > 5e7) & (c("age") > 250) & c("rm").is_not_null()).fill_null(False)
    ur = (base & c("rk_mv").is_between(1, 1500) & (c("age") >= 250) & c("turn60").is_not_null()).fill_null(False)
    df = df.with_columns(uh.alias("uh"), ur.alias("ur"))
    u = scores(df.filter(c("uh") | c("ur")))
    liqd = df.filter((c("amt20") > 5e7) & (c("age") > 250) & ~c("st")).select("code", "trade_date", "mvd")
    px = df.select("code", "trade_date", "ac", "ao", "lim_up_open", "lock_down").sort("code", "trade_date")
    return px, u, td, liqd


def factors(df):
    mkt = df.group_by("trade_date").agg(c("r").mean().alias("mkt"))
    s = df.filter(c("mvd_prev").is_not_null() & c("r").is_not_null())
    s = s.with_columns(c("mvd_prev").rank("ordinal").over("trade_date").alias("q"), pl.len().over("trade_date").alias("nn"))
    smb = s.group_by("trade_date").agg((c("r").filter(c("q") <= 0.3 * c("nn")).mean()
                                         - c("r").filter(c("q") > 0.7 * c("nn")).mean()).alias("smb"))
    return mkt.join(smb, on="trade_date", how="left").sort("trade_date")


def resid_mom(df, td):
    """residual momentum at month-ends (masked OLS, vectorised by normal equations)."""
    f = factors(df)
    days = td["trade_date"].to_list(); di = {d: i for i, d in enumerate(days)}
    f = td.select("trade_date").join(f, on="trade_date", how="left").sort("trade_date")
    F = np.column_stack([np.ones(len(days)), f["mkt"].fill_null(0).to_numpy(), f["smb"].fill_null(0).to_numpy()])
    wide = df.select("code", "trade_date", "r").drop_nulls("r")
    codes = sorted(wide["code"].unique().to_list()); ci = {k: i for i, k in enumerate(codes)}
    R = np.full((len(days), len(codes)), np.nan, dtype=np.float64)
    R[np.array([di[d] for d in wide["trade_date"].to_list()]), np.array([ci[k] for k in wide["code"].to_list()])] = wide["r"].to_numpy()
    me = [d for d in td.filter(c("is_me"))["trade_date"].to_list() if di[d] >= REG - 1]
    out = []
    for d in me:
        t = di[d]; Y = R[t - REG + 1:t + 1]; X = F[t - REG + 1:t + 1]
        W = ~np.isnan(Y); nreg = W.sum(0); Wf = W[:REG - SKIP]; nform = Wf.sum(0)
        ok = (nreg >= MIN_REG) & (nform >= MIN_FORM)
        if not ok.any(): continue
        Y0 = np.where(W, Y, 0.0)[:, ok]; Wk = W[:, ok].astype(np.float64)
        A = np.einsum("dn,di,dj->nij", Wk, X, X); b = np.einsum("dn,di->ni", Y0 * Wk, X)
        try:
            beta = np.linalg.solve(A, b[..., None])[..., 0]
        except np.linalg.LinAlgError:
            beta = np.stack([np.linalg.lstsq(A[i], b[i], rcond=None)[0] for i in range(A.shape[0])])
        E = (Y0 - X @ beta.T) * Wk
        Ef, Wff = E[:REG - SKIP], Wk[:REG - SKIP]; nf = Wff.sum(0)
        rmv = Ef.sum(0); mu = rmv / nf
        sd = np.sqrt((((Ef - mu) * Wff) ** 2).sum(0) / (nf - 1))
        cs = [codes[i] for i in np.flatnonzero(ok)]
        out.append(pl.DataFrame({"code": cs, "trade_date": [d] * len(cs), "rm": rmv, "rm_sd": sd}))
    o = pl.concat(out)
    return o.with_columns(pl.when(c("rm_sd") > 0).then(c("rm") / c("rm_sd")).otherwise(None).alias("rmlv"))


def _rank_by(u, cond, col, name):
    s = (u.filter(cond & c(col).is_not_null()).sort(["trade_date", col, "code"], descending=[False, True, False])
         .with_columns((pl.int_range(pl.len()).over("trade_date") + 1).alias(name)).select("code", "trade_date", name))
    return u.join(s, on=["code", "trade_date"], how="left")


def scores(u):
    u = u.select("code", "trade_date", "uh", "ur", "rk_mv", "mvd", "turn60", "vol60", "dv_ttm", "rm", "rmlv", "close").sort("trade_date", "code")
    u = _rank_by(u, c("uh"), "rm", "rk_rm")
    u = _rank_by(u, c("uh"), "rmlv", "rk_rmlv")
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
        print(ph, "signal months", len(sig_dates), "rm months", nrm.height, "U/month", round(us.filter(c("uh")).height / max(1, len(sig_dates))), flush=True)
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
                if nm == "RM_h20" and ph.split()[0] in ("R12", "N3"):
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
