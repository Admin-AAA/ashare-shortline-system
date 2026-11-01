"""[长线] §108 52周新高临近度月度组合（George & Hwang 2004 锚定）— 预注册 3 版（/workspace/strategy-research.md §108，结果出来前写定，无网格、不调参）。
基于 lt_research19 月度组合引擎（lt_research16.run）：月末收盘信号、次日开盘等权买、固定持有、已持有不重复买、不递补；
lt_data 窗口加载（LT_START/LT_END，+420 天预热），每个阶段组单独进程。
价格口径：只用 daily_kbar 缓存 / daily_hist 的 OHLC + pct_chg（不用 DB ma250、不用 adj_factor）。
  ac = pct_chg 累乘复权收盘；ah = high*ac/close；H250 = 信号日及之前 250 根 ah 最大值（不足 250 根为空）；ratio = ac / H250。
股池 U（月末收盘）：liq（20日均额>5000万、面板内上市>250 个交易日、非ST、非北交所）+ 收盘>=3 + 次一交易日有日K + ratio/vol60 非空。
  H52_h60  : U 内 ratio 从高到低前 10（同值 vol60 低优先、再按代码），持 60（容量 40，权益 1/30）
  H52LV_h60: U 内 vol60 < 当日 U 截面中位数，再按 ratio 前 10，持 60（容量 40，权益 1/30）
  H52_h20  : 同 H52_h60 选股，持 20（容量 20，权益 1/10）-> /mid
对照（判定）：cohort_bench —— 信号日 U 内同市值五分位全体，同入场/出场日开盘入→开盘出等权、扣 0.3%；超额 = 策略每笔 − 基准每笔。
对照（仅报告）：同五分位随机抽同只数，20 种子（§98/§102 口径）。
参照（不参与选择）：LTV_h60（§98 股池市值 200–1500）、DVLV_h60（§102 股池 1–1500），报告每笔与月均重叠只数；
  RB（long-repurchase-big）上界重叠：H52 成交中同股在 [入场-60 交易日, 出场] 出现过 RB 合格信号的占比；
  H52_h20 另报与 /mid EA_h20、F_any_h20（datafix 成交 CSV）持有期交集占比（仅 R12/N3）。
用法：LT_START=.. LT_END=.. python lt_research22.py "<phase>,<phase>" out.json"""
import sys, json, os
from collections import defaultdict
from pathlib import Path
import numpy as np
import polars as pl
import lt_data as L
from lt_research import PH
from realistic import add_limits, metrics_real, COST
from lt_research16 import run, expectancy, arrays
from lt_research19 import shares, streak, payoff
c = pl.col
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
NSEED = 20
OUTDIR = Path(__file__).resolve().parents[2] / "output/research/sprint4"
# name: (rank column, hold, capacity, notional slots, is_variant)
C = {"H52_h60": ("rk_h52", 60, 40, 30, True), "H52LV_h60": ("rk_h52lv", 60, 40, 30, True),
     "H52_h20": ("rk_h52", 20, 20, 10, True),
     "LTV_h60_ref": ("rk_ltv", 60, 40, 30, False), "DVLV_h60_ref": ("rk_dvlv", 60, 40, 30, False)}


def prep():
    df = L.prices().filter(~c("code").str.slice(0, 1).is_in(["4", "8", "9"]))
    df = df.with_columns(((1 + c("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code")).alias("ac"))
    df = df.with_columns((c("open") * c("ac") / c("close")).alias("ao"), (c("high") * c("ac") / c("close")).alias("ah"),
                         pl.int_range(pl.len()).over("code").alias("age"))
    df = df.with_columns(c("ah").rolling_max(250).over("code").alias("h250"),
                         c("amount").rolling_mean(20).over("code").alias("amt20"))
    df = df.with_columns((c("ac") / c("h250")).alias("ratio"))
    df = df.sort("trade_date").join_asof(shares(), on="trade_date", by="code", strategy="backward")
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.join_asof(st, on="trade_date", by="code", strategy="backward").with_columns(c("st").fill_null(False))
    df = df.with_columns((c("total_share") * c("close")).alias("mvd"),
                         (c("amount") / (c("float_share") * 1e4 * c("close"))).alias("tov")).sort("code", "trade_date")
    df = df.with_columns(c("tov").rolling_mean(60).over("code").alias("turn60"),
                         (c("pct_chg") / 100).clip(-0.35, 0.35).rolling_std(60).over("code").alias("vol60"),
                         c("trade_date").shift(-1).over("code").alias("nxt"))
    df = add_limits(df.drop("total_share", "float_share", "amount", "volume", "ah", "h250"))
    td = df.select("trade_date").unique().sort("trade_date")
    td = td.with_columns(c("trade_date").shift(-1).alias("mkt_nxt"), c("trade_date").shift(1).alias("mkt_prv"),
                         c("trade_date").str.slice(0, 7).alias("ym"))
    me = td.group_by("ym").agg(c("trade_date").max().alias("me"))
    td = td.join(me, on="ym").with_columns((c("trade_date") == c("me")).alias("is_me")).select("trade_date", "mkt_nxt", "mkt_prv", "is_me").sort("trade_date")
    df = df.join(td.select("trade_date", "mkt_nxt", "is_me"), on="trade_date", how="left")
    rk = pl.when(c("mvd").is_not_null()).then(-c("mvd")).rank("ordinal").over("trade_date")
    df = df.with_columns(rk.alias("rk_mv"))
    base = c("is_me") & ~c("st") & (c("close") >= 3) & (c("nxt") == c("mkt_nxt")) & c("vol60").is_not_null()
    uh = (base & (c("amt20") > 5e7) & (c("age") > 250) & c("ratio").is_not_null()).fill_null(False)
    ur = (base & c("rk_mv").is_between(1, 1500) & (c("age") >= 250) & c("turn60").is_not_null()).fill_null(False)
    df = df.with_columns(uh.alias("uh"), ur.alias("ur"))
    u = scores(df.filter(c("uh") | c("ur")))
    liqd = df.filter((c("amt20") > 5e7) & (c("age") > 250) & ~c("st")).select("code", "trade_date", "mvd")
    px = df.select("code", "trade_date", "ac", "ao", "lim_up_open", "lock_down").sort("code", "trade_date")
    return px, u, td, liqd


def _rank_by(u, cond, name):
    s = (u.filter(cond).sort(["trade_date", "ratio", "vol60", "code"], descending=[False, True, False, False])
         .with_columns((pl.int_range(pl.len()).over("trade_date") + 1).alias(name)).select("code", "trade_date", name))
    return u.join(s, on=["code", "trade_date"], how="left")


def scores(u):
    u = u.select("code", "trade_date", "uh", "ur", "rk_mv", "mvd", "turn60", "vol60", "dv_ttm", "ratio", "close").sort("trade_date", "code")
    # H52 variants (universe uh)
    u = u.with_columns(pl.when(c("uh")).then(c("vol60")).median().over("trade_date").alias("vmed"))
    u = _rank_by(u, c("uh"), "rk_h52")
    u = _rank_by(u, c("uh") & (c("vol60") < c("vmed")), "rk_h52lv")
    qr = pl.when(c("uh") & c("mvd").is_not_null()).then(c("mvd")).rank("ordinal").over("trade_date")
    nq = pl.when(c("uh") & c("mvd").is_not_null()).then(1).sum().over("trade_date")
    u = u.with_columns((qr * 5 / nq).ceil().clip(1, 5).cast(pl.Int32).alias("q"))
    # §98 LTV reference (universe ur & rank 200-1500), identical to lt_research19
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
    return u.drop("rt", "rv", "rvo", "rd", "rv2", "rvo2", "vmed")


def cohort(t, us, px, td):
    """same-signal-day, same-mcap-quintile (within U) equal-weight open->open benchmark, same entry/exit, minus COST."""
    q = us.filter(c("uh") & c("q").is_not_null()).select("code", "trade_date", "q")
    tt = (t.with_row_index("tid").join(td.select(c("trade_date").alias("entry"), c("mkt_prv").alias("sig")), on="entry", how="left")
           .join(q.rename({"trade_date": "sig"}), on=["code", "sig"], how="left"))
    p = px.select("code", "trade_date", "ao")
    coh = (tt.select("tid", "sig", "q", "entry", "exit").join(q.rename({"trade_date": "sig", "code": "m"}), on=["sig", "q"])
             .join(p.rename({"trade_date": "entry", "code": "m", "ao": "a0"}), on=["m", "entry"])
             .join(p.rename({"trade_date": "exit", "code": "m", "ao": "a1"}), on=["m", "exit"])
             .group_by("tid").agg((c("a1") / c("a0") - 1 - COST).clip(-0.9, 5).mean().alias("bench"), pl.len().alias("nc")))
    return tt.join(coh, on="tid", how="left")


def rb_signals(td, liqd):
    from lt_research12 import repo_sig
    r = repo_sig(td.select("trade_date")).join(liqd, on=["code", "trade_date"], how="inner")
    return r.filter((c("rp_amt") / (c("mvd") * 1e4)) >= 0.01).select("code", "trade_date")


def rb_overlap(t, rb, td):
    ti = {d: i for i, d in enumerate(td["trade_date"].to_list())}
    ev = defaultdict(list)
    for k, d in rb.iter_rows():
        if d in ti: ev[k].append(ti[d])
    hit = 0
    for k, en, ex in t.select("code", "entry", "exit").iter_rows():
        a, b = ti[en] - 60, ti[ex]
        if any(a <= x <= b for x in ev.get(k, [])): hit += 1
    return round(hit / t.height, 3) if t.height else 0.0


def hold_overlap(t, path):
    if not os.path.exists(path) or t is None or t.height == 0: return None
    e = pl.read_csv(path, schema_overrides={"code": pl.Utf8}).with_columns(c("code").str.zfill(6))
    tt = t.with_row_index("tid")
    j = tt.join(e.select("code", c("entry").cast(pl.Utf8).alias("e0"), c("exit").cast(pl.Utf8).alias("e1")), on="code")
    ov = j.filter((c("entry") < c("e1")) & (c("e0") < c("exit")))["tid"].n_unique()
    return round(ov / t.height, 3)


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
        print(ph, "signal months", len(sig_dates), "U/month", round(us.filter(c("uh")).height / max(1, len(sig_dates))),
              "ratio==1/month", round(us.filter(c("uh") & (c("ratio") >= 0.99999)).height / max(1, len(sig_dates)), 1), flush=True)
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
                m["exp"] = expectancy(t); m["max_loss_streak"] = streak(t); m.update(payoff(t))
            for rn, ss in sets.items():
                m[f"ovl_{rn}"] = round(float(np.mean([len(set(by.get(d, [])) & ss.get(d, set())) for d in sig_dates])), 2)
            m["mean_rk_mv"] = round(float(pk["rk_mv"].mean()), 0) if pk.height else None
            m["mean_ratio"] = round(float(pk["ratio"].mean()), 4) if pk.height and var else None
            m["last_picks"] = {d: by[d] for d in sig_dates[-2:]}
            if var and t is not None:
                b = cohort(t, us, px, td)
                m["bench"] = round(100 * float(b["bench"].mean()), 2); m["excess"] = round(m["avg"] - m["bench"], 2)
                m["ex_win"] = round(100 * float(((b["ret"] - b["bench"]) > 0).mean()), 1)
                m["bench_cover"] = round(float(b["bench"].is_not_null().mean()), 3)
                m["ovl_RB_upper"] = rb_overlap(t, rb, td)
                if nm == "H52_h20" and ph.split()[0] in ("R12", "N3"):
                    dd = OUTDIR / "datafix"
                    for ref in ("EA_h20", "F_any_h20"):
                        m[f"hold_ovl_{ref}"] = hold_overlap(t, str(dd / f"lt20_{ref}_{ph.split()[0]}_trades.csv"))
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
