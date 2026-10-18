"""[长线] §102 红利低波：股息率 dv_ttm 月度组合 — 预注册 3 版（结果出来前写定，无网格、不调参）。
基于 lt_research16：lt_data 窗口加载（LT_START/LT_END，+420 天预热），每个阶段单独进程；8 阶段 + R12 + N3。
股池（每月最后一个交易日收盘）：非北交所按折算日总市值（周度 total_share × 当日不复权收盘）从大到小排名第 1–1500，
  非 ST、面板内上市 ≥250 个交易日、收盘 ≥3 元、次一交易日有日K；次日开盘涨停 -> 引擎跳过且不递补（无前视）。
dv_ttm（PIT）：信号日当天或之前最近一个周度 daily_basic 快照（join_asof backward，不折算）；只用 dv_ttm>0。
  DV_h60  : dv_ttm 从高到低前 10 只（同值按代码），次日开盘等权买，持 60 日（容量 40，权益 1/30 名义/笔）
  DVLV_h60: dv_ttm>0 子集内 dv_ttm 秩（高好）+ vol60 秩（低好）之和最低 10 只（同分 vol60 低优先），持 60 日（容量 40，权益 1/30）
  DV_h20  : 同 DV_h60 选股，持 20 日（容量 20，权益 1/10）
  参照 LTV_h60（§98 原口径，股池 200–1500，不是变体）：不跑对照，只给每笔基准与月均重叠只数。
对照：同股池 1–1500（含不分红）按市值 5 档（1-300/301-600/601-900/901-1200/1201-1500）同档随机抽同只数，20 个种子平均。
成本：realistic COST=0.3% 往返；开盘涨停不买、跌停锁死顺延、逐日盯市。
用法：LT_START=.. LT_END=.. python lt_research19.py "<phase>" out.json"""
import sys, json, os
from collections import defaultdict
import numpy as np
import polars as pl
import lt_data as L
from lt_research import PH
from realistic import add_limits, metrics_real
from lt_research16 import run, expectancy, arrays
c = pl.col
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
BUCKETS = [(1, 300), (301, 600), (601, 900), (901, 1200), (1201, 1500)]
NSEED = 20
# name: (rank column, hold, capacity, notional slots, run control)
C = {"DV_h60": ("rk_dv", 60, 40, 30, True), "DVLV_h60": ("rk_dvlv", 60, 40, 30, True),
     "DV_h20": ("rk_dv", 20, 20, 10, True), "LTV_h60_ref": ("rk_ltv", 60, 40, 30, False)}


def shares(start=None, end=None):
    """total_share / float_share (万股) + dv_ttm(%) from weekly daily_basic, same windowing as lt_data.basic."""
    start, end = L._range(start, end)
    fs = [f for f in sorted(L.D.glob("basic_[0-9]*.parquet")) if not start or f.stem[-4:] >= start[:4]]
    b = pl.concat([pl.read_parquet(f, columns=["ts_code", "trade_date", "total_share", "float_share", "dv_ttm"])
                   .with_columns(c("trade_date").cast(pl.Utf8), c("dv_ttm").cast(pl.Float64, strict=False))
                   for f in fs], how="diagonal_relaxed")
    b = b.filter(L._win("trade_date", start, end, dash=False))
    return b.select(c("ts_code").str.slice(0, 6).alias("code"), L._dash("trade_date").alias("trade_date"),
                    c("total_share").cast(pl.Float64), c("float_share").cast(pl.Float64),
                    c("dv_ttm")).unique(["code", "trade_date"]).sort("trade_date")


def prep():
    df = L.prices().filter(~c("code").str.slice(0, 1).is_in(["4", "8", "9"]))
    df = df.with_columns(((1 + c("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code")).alias("ac"))
    df = df.with_columns((c("open") * c("ac") / c("close")).alias("ao"), pl.int_range(pl.len()).over("code").alias("age"))
    df = df.sort("trade_date").join_asof(shares(), on="trade_date", by="code", strategy="backward")
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.join_asof(st, on="trade_date", by="code", strategy="backward").with_columns(c("st").fill_null(False))
    df = df.with_columns((c("total_share") * c("close")).alias("mvd"),
                         (c("amount") / (c("float_share") * 1e4 * c("close"))).alias("tov")).sort("code", "trade_date")
    df = df.with_columns(c("tov").rolling_mean(60).over("code").alias("turn60"),
                         (c("pct_chg") / 100).clip(-0.35, 0.35).rolling_std(60).over("code").alias("vol60"),
                         c("trade_date").shift(-1).over("code").alias("nxt"))
    df = add_limits(df.drop("total_share", "float_share", "amount", "volume"))
    td = df.select("trade_date").unique().sort("trade_date")
    td = td.with_columns(c("trade_date").shift(-1).alias("mkt_nxt"), c("trade_date").str.slice(0, 7).alias("ym"))
    me = td.group_by("ym").agg(c("trade_date").max().alias("me"))
    td = td.join(me, on="ym").with_columns((c("trade_date") == c("me")).alias("is_me")).select("trade_date", "mkt_nxt", "is_me").sort("trade_date")
    df = df.join(td, on="trade_date", how="left")
    rk = pl.when(c("mvd").is_not_null()).then(-c("mvd")).rank("ordinal").over("trade_date")
    df = df.with_columns(rk.alias("rk_mv"))
    uni = (c("is_me") & c("rk_mv").is_between(1, 1500) & ~c("st") & (c("age") >= 250) & (c("close") >= 3)
           & (c("nxt") == c("mkt_nxt")) & c("turn60").is_not_null() & c("vol60").is_not_null()).fill_null(False)
    df = df.with_columns(uni.alias("uni"))
    u = uni_scores(df.filter(c("uni")))
    px = df.select("code", "trade_date", "ac", "ao", "lim_up_open", "lock_down").sort("code", "trade_date")
    return px, u, td


def uni_scores(u):
    u = u.select("code", "trade_date", "rk_mv", "turn60", "vol60", "dv_ttm", "close").sort("trade_date", "code")
    # §98 LTV reference (universe 200-1500, identical ranking formula)
    lt = c("rk_mv") >= 200
    u = u.with_columns(pl.when(lt).then(c("turn60")).rank("average").over("trade_date").alias("rt"),
                       pl.when(lt).then(c("vol60")).rank("average").over("trade_date").alias("rv"),
                       pl.when(lt).then(c("vol60")).rank("ordinal").over("trade_date").alias("rvo"))
    u = u.with_columns(pl.when(lt).then(c("rt") + c("rv") + c("rvo") * 1e-6).rank("ordinal").over("trade_date").alias("rk_ltv"))
    # dividend variants (dv_ttm > 0 subset)
    dv = c("dv_ttm").is_not_null() & (c("dv_ttm") > 0)
    u = u.with_columns(pl.when(dv).then(-c("dv_ttm")).rank("ordinal").over("trade_date").alias("rk_dv"),  # ties -> code order
                       pl.when(dv).then(-c("dv_ttm")).rank("average").over("trade_date").alias("rd"),
                       pl.when(dv).then(c("vol60")).rank("average").over("trade_date").alias("rv2"),
                       pl.when(dv).then(c("vol60")).rank("ordinal").over("trade_date").alias("rvo2"))
    u = u.with_columns(pl.when(dv).then(c("rd") + c("rv2") + c("rvo2") * 1e-6).rank("ordinal").over("trade_date").alias("rk_dvlv"))
    bk = pl.lit(None, pl.Int32)
    for i, (lo, hi) in enumerate(BUCKETS):
        bk = pl.when(c("rk_mv").is_between(lo, hi)).then(pl.lit(i, pl.Int32)).otherwise(bk)
    return u.with_columns(bk.alias("bkt"))


def streak(t):
    r = t.sort("entry", "code")["ret"].to_numpy(); best = cur = 0
    for x in r:
        cur = cur + 1 if x <= 0 else 0; best = max(best, cur)
    return best


def payoff(t):
    r = t["ret"].to_numpy(); w, l = r[r > 0], r[r <= 0]
    return {"avg_win": round(100 * float(w.mean()), 2) if len(w) else 0.0,
            "avg_loss": round(100 * float(l.mean()), 2) if len(l) else 0.0,
            "payoff": round(float(w.mean() / -l.mean()), 2) if len(w) and len(l) and l.mean() < 0 else None}


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    px, u, td = prep()
    print("built", px.height, "uni rows", u.height, flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    me_all = td.filter(c("is_me") & c("mkt_nxt").is_not_null())
    for ph in phases:
        s, e = PH[ph]
        sig_dates = me_all.filter((c("mkt_nxt") >= s) & (c("mkt_nxt") <= e))["trade_date"].to_list()
        codes, idx = arrays(px, sig_dates[0])
        us = u.filter(c("trade_date").is_in(sig_dates))
        pool = {(d, b): g["code"].to_list() for (d, b), g in us.filter(c("bkt").is_not_null()).sort("code").group_by(["trade_date", "bkt"])}
        ndv = us.filter(c("rk_dv").is_not_null()).height
        print(ph, "signal months", len(sig_dates), "uni/month", round(us.height / max(1, len(sig_dates))),
              "dv>0/month", round(ndv / max(1, len(sig_dates))), flush=True)
        ltv_sets = {d: set(g["code"].to_list()) for (d,), g in us.filter(c("rk_ltv") <= 10).group_by(["trade_date"])}
        for nm, (col, hold, kc, kn, ctrl) in C.items():
            pk = us.filter(c(col) <= 10).sort("trade_date", col)
            by = defaultdict(list); cnt = defaultdict(lambda: defaultdict(int))
            for code, d, b in pk.select("code", "trade_date", "bkt").iter_rows():
                by[d].append(code); cnt[d][b] += 1
            t, cur = run(codes, idx, by, sig_dates, hold, kc, kn)
            m = {a: float(v) for a, v in metrics_real(t, cur).items()}
            if t is not None:
                m["exp"] = expectancy(t); m["max_loss_streak"] = streak(t); m.update(payoff(t))
            m["ovl_ltv"] = round(float(np.mean([len(set(by.get(d, [])) & ltv_sets.get(d, set())) for d in sig_dates])), 2)
            m["mean_rk_mv"] = round(float(pk["rk_mv"].mean()), 0) if pk.height else None
            m["mean_dv"] = round(float(pk["dv_ttm"].mean()), 2) if pk.height else None
            m["last_picks"] = {d: by[d] for d in sig_dates[-2:]}
            if ctrl:
                cs = []
                for seed in range(NSEED):
                    byr = {}
                    for d in sig_dates:
                        rng = np.random.default_rng([seed, int(d.replace("-", ""))])
                        lst = []
                        for b, k in sorted(cnt[d].items()):
                            p = pool.get((d, b), [])
                            if p: lst += [str(x) for x in rng.choice(p, size=min(k, len(p)), replace=False)]
                        rng.shuffle(lst); byr[d] = lst
                    tc, cc = run(codes, idx, byr, sig_dates, hold, kc, kn)
                    cs.append(metrics_real(tc, cc))
                av = [float(x.get("avg", 0)) for x in cs]
                ca = float(np.mean(av))
                m.update({"ctrl_avg": round(ca, 2), "ctrl_tot": round(float(np.mean([x.get("tot", 0) for x in cs])), 1),
                          "ctrl_mdd": round(float(np.mean([x.get("mdd", 0) for x in cs])), 1),
                          "ctrl_pf": round(float(np.mean([x.get("pf", 0) for x in cs])), 2),
                          "ctrl_avg_p10": round(float(np.percentile(av, 10)), 2), "ctrl_avg_p90": round(float(np.percentile(av, 90)), 2),
                          "exc": round(m.get("avg", 0) - ca, 2)})
            res.setdefault(nm, {})[ph] = m
            print(nm, ph, {k: v for k, v in m.items() if k != "last_picks"}, flush=True)
        json.dump(res, open(out, "w"), indent=1)
        del codes, idx
    print("done", flush=True)
