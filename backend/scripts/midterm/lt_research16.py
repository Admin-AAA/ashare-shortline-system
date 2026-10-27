"""[长线] §98 低换手 + 低波动月度组合（防御型异象）— 预注册 3 版（结果出来前写定，无网格、不调参）。
基于 lt_research15：lt_data 窗口加载（LT_START/LT_END，+420 天预热），每个阶段组单独进程；8 阶段 + R12 + N3。
股池（每月最后一个交易日收盘）：非北交所全部有市值股票按折算日总市值（total_share×不复权收盘）从大到小排名第 200–1500，
  非 ST、面板内上市 ≥250 个交易日、收盘 ≥3 元、次一交易日有日K（未停牌）；次日开盘涨停 -> 引擎跳过且不递补（无前视）。
特征：换手=成交额/(float_share×收盘)；turn60=60日均换手；vol60=60日日收益标准差；r120x20=t-120..t-20 复权收益。
  LTV_h60   : 分 = turn60 截面秩 + vol60 截面秩（都低好），取最低 10 只，次日开盘等权买，持 60 日（容量 40，权益 1/30 名义/笔）
  LTV_h20   : 同选股，持 20 日（容量 20，权益 1/10 名义/笔）
  LV_MOM_h60: vol60 最低 30 只里 r120x20 最高 10 只，持 60 日（容量 40，权益 1/30）
对照：同股池、市值排名 5 档（200-459/460-719/720-979/980-1239/1240-1500）按变体当月各档只数同档随机抽，20 个种子平均。
成本：realistic COST=0.3% 往返；开盘涨停不买、跌停锁死顺延、逐日盯市。
采纳标准（用户新权重）：R12 均值>0 且总>0；N3 均值>0、总>0、MDD>-35%；2024 与 2025-01~09 每笔 >-1%；
  每笔均值优于对照：R12 与 N3 均优于且 8 阶段 >=5；老阶段每笔 >-5% 且 MDD >-40%；R12 或 N3 PF>=1.3；R12/N3 笔数 >=30。
用法：LT_START=.. LT_END=.. python lt_research16.py "<phase>,<phase>" out.json"""
import sys, json, os
from collections import defaultdict
import numpy as np
import polars as pl
import lt_data as L
from lt_research import PH
from realistic import add_limits, metrics_real, COST
c = pl.col
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
BUCKETS = [(200, 459), (460, 719), (720, 979), (980, 1239), (1240, 1500)]
NSEED = 20
# name: (selection, hold, capacity, notional slots)
C = {"LTV_h60": ("ltv", 60, 40, 30), "LTV_h20": ("ltv", 20, 20, 10), "LV_MOM_h60": ("lvm", 60, 40, 30)}


def shares(start=None, end=None):
    """total_share / float_share (万股) from weekly daily_basic, same windowing as lt_data.basic."""
    start, end = L._range(start, end)
    fs = [f for f in sorted(L.D.glob("basic_[0-9]*.parquet")) if not start or f.stem[-4:] >= start[:4]]
    b = pl.concat([pl.read_parquet(f, columns=["ts_code", "trade_date", "total_share", "float_share"])
                   .with_columns(c("trade_date").cast(pl.Utf8)) for f in fs], how="diagonal_relaxed")
    b = b.filter(L._win("trade_date", start, end, dash=False))
    return b.select(c("ts_code").str.slice(0, 6).alias("code"), L._dash("trade_date").alias("trade_date"),
                    c("total_share").cast(pl.Float64), c("float_share").cast(pl.Float64)).unique(["code", "trade_date"]).sort("trade_date")


def prep():
    df = L.prices().filter(~c("code").str.slice(0, 1).is_in(["4", "8", "9"]))
    df = df.with_columns(((1 + c("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code")).alias("ac"))
    df = df.with_columns((c("open") * c("ac") / c("close")).alias("ao"), pl.int_range(pl.len()).over("code").alias("age"))
    df = df.sort("trade_date").join_asof(shares(), on="trade_date", by="code", strategy="backward")
    st = pl.read_parquet(__import__("os").environ.get("ST_HIST_PATH") or (L.D / "st_hist.parquet")).rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.join_asof(st, on="trade_date", by="code", strategy="backward").with_columns(c("st").fill_null(False))
    df = df.with_columns((c("total_share") * c("close")).alias("mvd"),
                         (c("amount") / (c("float_share") * 1e4 * c("close"))).alias("tov")).sort("code", "trade_date")
    df = df.with_columns(c("tov").rolling_mean(60).over("code").alias("turn60"),
                         (c("pct_chg") / 100).clip(-0.35, 0.35).rolling_std(60).over("code").alias("vol60"),
                         (c("ac").shift(20).over("code") / c("ac").shift(120).over("code") - 1).alias("r120x20"),
                         c("trade_date").shift(-1).over("code").alias("nxt"))
    df = add_limits(df.drop("total_share", "float_share", "amount", "volume"))
    td = df.select("trade_date").unique().sort("trade_date")
    td = td.with_columns(c("trade_date").shift(-1).alias("mkt_nxt"), c("trade_date").str.slice(0, 7).alias("ym"))
    me = td.group_by("ym").agg(c("trade_date").max().alias("me"))
    td = td.join(me, on="ym").with_columns((c("trade_date") == c("me")).alias("is_me")).select("trade_date", "mkt_nxt", "is_me").sort("trade_date")
    df = df.join(td, on="trade_date", how="left")
    rk = pl.when(c("mvd").is_not_null()).then(-c("mvd")).rank("ordinal").over("trade_date")
    df = df.with_columns(rk.alias("rk_mv"))
    uni = (c("is_me") & c("rk_mv").is_between(200, 1500) & ~c("st") & (c("age") >= 250) & (c("close") >= 3)
           & (c("nxt") == c("mkt_nxt")) & c("turn60").is_not_null() & c("vol60").is_not_null()).fill_null(False)
    df = df.with_columns(uni.alias("uni"))
    u = uni_scores(df.filter(c("uni")))
    px = df.select("code", "trade_date", "ac", "ao", "lim_up_open", "lock_down").sort("code", "trade_date")
    return px, u, td


def uni_scores(u):
    u = u.select("code", "trade_date", "rk_mv", "turn60", "vol60", "r120x20", "close")
    u = u.with_columns(c("turn60").rank("average").over("trade_date").alias("rt"),
                       c("vol60").rank("average").over("trade_date").alias("rv"),
                       c("vol60").rank("ordinal").over("trade_date").alias("rvo"))
    u = u.with_columns((c("rt") + c("rv") + c("rvo") * 1e-6).rank("ordinal").over("trade_date").alias("rk_ltv"))
    u = u.with_columns(pl.when((c("rvo") <= 30) & c("r120x20").is_not_null()).then(-c("r120x20")).rank("ordinal").over("trade_date").alias("rk_lvm"))
    bk = pl.lit(None, pl.Int32)
    for i, (lo, hi) in enumerate(BUCKETS):
        bk = pl.when(c("rk_mv").is_between(lo, hi)).then(pl.lit(i, pl.Int32)).otherwise(bk)
    return u.with_columns(bk.alias("bkt"))


def arrays(px, start):
    sub = px.filter(c("trade_date") >= start)
    codes = {code: {col: part[col].to_numpy() for col in part.columns if col != "code"}
             for (code,), part in sub.partition_by("code", as_dict=True, maintain_order=True).items()}
    idx = {k: {d: i for i, d in enumerate(a["trade_date"])} for k, a in codes.items()}
    return codes, idx


def run(codes, idx, by_date, sig_dates, hold, k_cap, k_norm):
    """realistic.run_real logic with precomputed arrays; signal at close d (month-end), buy next open, fixed hold."""
    held, trades, pnl = {}, [], defaultdict(float)
    for d in sig_dates:
        held = {k: x for k, x in held.items() if x > d}
        for k in [k for k in by_date.get(d, []) if k not in held][:10]:
            if len(held) >= k_cap: break
            if k not in codes or d not in idx[k]: continue
            a = codes[k]; n = len(a["trade_date"]); e = idx[k][d] + 1
            if e >= n or a["lim_up_open"][e]: continue
            p0 = a["ao"][e]; xi = min(n - 1, e + hold)
            while xi < n - 1 and a["lock_down"][xi]: xi += 1
            prev = p0
            for j in range(e, xi):
                pnl[a["trade_date"][j]] += (a["ac"][j] - prev) / p0; prev = a["ac"][j]
            pnl[a["trade_date"][xi]] += (a["ao"][xi] - prev) / p0 - COST
            trades.append((k, a["trade_date"][e], a["trade_date"][xi], xi - e, a["ao"][xi] / p0 - 1 - COST))
            held[k] = a["trade_date"][xi]
    if not trades: return None, None
    t = pl.DataFrame(trades, schema=["code", "entry", "exit", "days", "ret"], orient="row")
    ds = sorted(pnl); eq = 1 + np.cumsum([pnl[x] / k_norm for x in ds])
    return t, (ds, eq)


def expectancy(t):
    r = t["ret"].to_numpy(); w, l = r[r > 0], r[r <= 0]
    return round(100 * ((len(w) / len(r)) * (w.mean() if len(w) else 0) - (len(l) / len(r)) * (-l.mean() if len(l) else 0)), 3)


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
        print(ph, "signal months", len(sig_dates), "uni/month", round(us.height / max(1, len(sig_dates))), flush=True)
        for nm, (sel, hold, kc, kn) in C.items():
            col = "rk_ltv" if sel == "ltv" else "rk_lvm"
            pk = us.filter(c(col) <= 10).sort("trade_date", col)
            by = defaultdict(list); cnt = defaultdict(lambda: defaultdict(int))
            for code, d, b in pk.select("code", "trade_date", "bkt").iter_rows():
                by[d].append(code); cnt[d][b] += 1
            t, cur = run(codes, idx, by, sig_dates, hold, kc, kn)
            m = {a: float(v) for a, v in metrics_real(t, cur).items()}
            if t is not None: m["exp"] = expectancy(t)
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
                      "exc": round(m.get("avg", 0) - ca, 2),
                      "mean_rk_mv": round(float(pk["rk_mv"].mean()), 0) if pk.height else None})
            res.setdefault(nm, {})[ph] = m
            print(nm, ph, m, flush=True)
        json.dump(res, open(out, "w"), indent=1)
        del codes, idx
    print("done", flush=True)
