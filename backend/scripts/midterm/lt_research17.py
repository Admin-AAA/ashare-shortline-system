"""[短线/中线/长线] §99 大额解禁回避过滤 — mid/long 部分（预注册写于 /workspace/strategy-research.md §99，结果出来前写定，不调参）。
解禁口径同 app/services/unlock_filter.py：share_floats quantdata 行，(code,float_date,holder,round(share)) 去重取最早 ann_date，
float_date ∈ (T, T 后第 N 个交易日] 求 float_ratio 和（占总股本 %）；主口径 PIT = ann_date ≤ T；敏感性 = 忽略 ann_date（仅参考）。
  U1 : 20 个交易日内合计 ≥ 3% → 跳过
  U2 : 持有期内（mid h20 → 20 日，long h60 → 60 日）合计 ≥ 1% → 跳过
现行 /mid /long 策略（口径同各自原研究）：
  mid-small-cap-weekly (lt_research11 live)、mid-repurchase-plan / long-repurchase-big (lt_research12 RP/RB)、
  mid-earnings-accel (lt_research15 EA_h20)、long-lowturn-lowvol (lt_research16 LTV_h60)。
过滤语义：事件型 = 从候选池删除（后续候选按排序补位）；排名型（小市值周度、LTV）= 前 10 只中被过滤的不买不递补。
另报：基线成交中会被过滤掉的那部分（笔数、每笔均值）。
用法：LT_START=.. LT_END=.. python lt_research17.py core|ltv "<phase>,<phase>" out.json   (解禁事件 parquet: UNLOCK_EV, 默认 /tmp/unlock_events.parquet)"""
import sys, json, os, datetime as _dt
from collections import defaultdict
import numpy as np
import polars as pl
from lt_research import build, fixed, PH
from realistic import run_real, metrics_real
c = pl.col
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
EV = os.environ.get("UNLOCK_EV", "/tmp/unlock_events.parquet")


def td_ends(td, ns):
    """trade_date -> YYYYMMDD of the n-th trading day after it (weekday extension past panel end)."""
    ds = list(td)
    last = _dt.date.fromisoformat(ds[-1]); ext = []
    while len(ext) < max(ns) + 5:
        last += _dt.timedelta(days=1)
        if last.weekday() < 5: ext.append(last.isoformat())
    full = ds + ext
    return pl.DataFrame({"trade_date": ds, **{f"end{n}": [full[i + n].replace("-", "") for i in range(len(ds))] for n in ns}})


def unlock_cols(sig, td, ns=(20, 60)):
    """sig: DataFrame(code, trade_date) -> adds s{n}_pit, s{n}_all (sum float_ratio %)."""
    ev = pl.read_parquet(EV)
    s = sig.select("code", "trade_date").unique().join(td_ends(td, ns), on="trade_date", how="left")
    s = s.with_columns(c("trade_date").str.replace_all("-", "").alias("t"))
    j = s.join(ev, on="code", how="inner")
    aggs = []
    for n in ns:
        w = (c("fd") > c("t")) & (c("fd") <= c(f"end{n}"))
        aggs += [pl.when(w & (c("ann") <= c("t"))).then(c("r")).otherwise(0).sum().alias(f"s{n}_pit"),
                 pl.when(w).then(c("r")).otherwise(0).sum().alias(f"s{n}_all")]
    a = j.group_by("code", "trade_date").agg(aggs)
    out = s.select("code", "trade_date").join(a, on=["code", "trade_date"], how="left")
    return out.with_columns([c(x).fill_null(0.0) for x in out.columns if x.startswith("s")])


def variants(h):
    hn = 20 if h <= 20 else 60
    return {"U1": (c("s20_pit") >= 3), "U2": (c(f"s{hn}_pit") >= 1),
            "U1_all": (c("s20_all") >= 3), "U2_all": (c(f"s{hn}_all") >= 1)}


def removed_stats(t, flags, td, vexpr):
    """baseline trades whose signal day (trading day before entry) is flagged."""
    if t is None or t.height == 0: return {"n_rm": 0}
    prev = td.select(c("trade_date").alias("entry"), c("trade_date").shift(1).alias("trade_date"))
    x = t.join(prev, on="entry", how="left").join(flags, on=["code", "trade_date"], how="left")
    x = x.with_columns([c(k).fill_null(0.0) for k in flags.columns if k.startswith("s")])
    rm = x.filter(vexpr)["ret"].to_numpy(); kp = x.filter(~vexpr)["ret"].to_numpy()
    return {"n_rm": int(len(rm)), "avg_rm": round(100 * float(rm.mean()), 2) if len(rm) else None,
            "win_rm": round(100 * float((rm > 0).mean()), 1) if len(rm) else None,
            "avg_kept": round(100 * float(kp.mean()), 2) if len(kp) else None}


def prep_core():
    from lt_research12 import repo_sig
    from lt_research14 import fina_sig
    df = build()
    td = df.select("trade_date").unique().sort("trade_date")
    df = df.join(fina_sig(td), on=["code", "trade_date"], how="left").join(repo_sig(td), on=["code", "trade_date"], how="left")
    G = c("liq") & (c("pdd") > 0) & (c("dtp") >= 50) & (c("orv") >= 20) & (c("dtp") > c("prev_dtp"))
    rp_ratio = c("rp_amt") / (c("total_mv").cast(pl.Float64, strict=False) * 1e4)
    df = df.with_columns(G.fill_null(False).alias("G"), c("dtp").clip(-1e9, 300).fill_null(0).alias("sc_g"),
                         (c("liq") & c("rp").fill_null(False)).fill_null(False).alias("RP"),
                         (c("liq") & c("rp").fill_null(False) & (rp_ratio >= 0.01)).fill_null(False).alias("RB"),
                         rp_ratio.fill_null(0).alias("sc_r"))
    # mid-small-cap-weekly live (lt_research11 S_live)
    px = df.select("code", c("trade_date").alias("bd"), c("ac").alias("ac_bd"))
    df = df.join(px, on=["code", "bd"], how="left").with_columns(
        (c("total_mv").cast(pl.Float64, strict=False) * c("ac") / c("ac_bd")).alias("mvd")).drop("ac_bd")
    g = (c("mvd").is_not_null() & (c("pe_ttm") > 0) & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"]))
    r = df.filter(g).with_columns(c("mvd").rank("ordinal").over("trade_date").alias("r4"))
    idx = (r.filter(c("r4") <= 400).group_by("trade_date").agg(c("pct_chg").clip(-20, 20).mean().alias("s")).sort("trade_date")
           .with_columns((1 + c("s") / 100).cum_prod().alias("scid")).with_columns(c("scid").rolling_mean(20).alias("scid20"))
           .select("trade_date", "scid", "scid20"))
    df = df.join(idx, on="trade_date", how="left")
    wk = (c("trade_date").str.to_date().dt.weekday() == 5)
    uni = ((c("amt20") > 3e7) & (c("age") > 250) & ~c("st") & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
           & c("mvd").is_not_null() & (c("pe_ttm") > 0)).fill_null(False)
    df = df.with_columns(pl.when(uni).then(c("mvd")).rank("ordinal").over("trade_date").alias("rk_live"), (-c("mvd")).alias("sc_small"))
    df = df.with_columns((wk & (c("scid") > c("scid20")) & (c("rk_live") <= 10)).fill_null(False).alias("S_live")).sort("code", "trade_date")
    sig = df.filter(c("S_live") | c("G") | c("RP")).select("code", "trade_date")
    fl = unlock_cols(sig, td["trade_date"].to_list())
    df = df.join(fl, on=["code", "trade_date"], how="left").with_columns(
        [c(x).fill_null(0.0) for x in fl.columns if x.startswith("s") and x != "sc_small"]).sort("code", "trade_date")
    return df, td, fl


CORE = {"mid-small-cap-weekly": ("S_live", "sc_small", 20, 20, 10), "mid-repurchase-plan": ("RP", "sc_r", 20, 20, 5),
        "mid-earnings-accel": ("G", "sc_g", 20, 20, 5), "long-repurchase-big": ("RB", "sc_r", 60, 20, 5)}


def run_core(phases, out):
    df, td, fl = prep_core()
    print("built", df.height, "flag rows", fl.height, flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for sid, (sig, sc, h, k, mn) in CORE.items():
        V = variants(h)
        for ph in phases:
            s, e = PH[ph]
            t0, cur0 = run_real(df, sig, sc, fixed(h), s, e, k_slots=k, max_new=mn, max_hold=h)
            m = {"base": {a: float(v) for a, v in metrics_real(t0, cur0).items()}}
            for vn, ex in V.items():
                df2 = df.with_columns((c(sig) & ~ex).alias("_S"))
                t, cur = run_real(df2, "_S", sc, fixed(h), s, e, k_slots=k, max_new=mn, max_hold=h)
                mm = {a: float(v) for a, v in metrics_real(t, cur).items()}
                mm.update(removed_stats(t0, fl, td, ex))
                m[vn] = mm
            res.setdefault(sid, {})[ph] = m
            print(sid, ph, json.dumps(m, ensure_ascii=False), flush=True)
        json.dump(res, open(out, "w"), indent=1)


def run_ltv(phases, out):
    import lt_research16 as L16
    px, u, td = L16.prep()
    me_all = td.filter(c("is_me") & c("mkt_nxt").is_not_null())
    pk_all = u.filter(c("rk_ltv") <= 10).select("code", "trade_date", "rk_ltv")
    tdl = td["trade_date"].to_list()
    fl = unlock_cols(pk_all, tdl)
    pk_all = pk_all.join(fl, on=["code", "trade_date"], how="left")
    tdf = td.select("trade_date")
    res = json.load(open(out)) if os.path.exists(out) else {}
    V = variants(60)
    for ph in phases:
        s, e = PH[ph]
        sig_dates = me_all.filter((c("mkt_nxt") >= s) & (c("mkt_nxt") <= e))["trade_date"].to_list()
        codes, idx = L16.arrays(px, sig_dates[0])
        pk = pk_all.filter(c("trade_date").is_in(sig_dates)).sort("trade_date", "rk_ltv")
        m = {}
        t0 = None
        for vn in ["base"] + list(V):
            q = pk if vn == "base" else pk.filter(~V[vn])
            by = defaultdict(list)
            for code, d in q.select("code", "trade_date").iter_rows():
                by[d].append(code)
            t, cur = L16.run(codes, idx, by, sig_dates, 60, 40, 30)
            mm = {a: float(v) for a, v in metrics_real(t, cur).items()}
            if vn == "base": t0 = t
            else: mm.update(removed_stats(t0, fl, tdf, V[vn]))
            m[vn] = mm
        res.setdefault("long-lowturn-lowvol", {})[ph] = m
        print("long-lowturn-lowvol", ph, json.dumps(m, ensure_ascii=False), flush=True)
        json.dump(res, open(out, "w"), indent=1)
        del codes, idx


if __name__ == "__main__":
    mode, phases, out = sys.argv[1], sys.argv[2].split(","), sys.argv[3]
    (run_core if mode == "core" else run_ltv)(phases, out)
    print("done", flush=True)
