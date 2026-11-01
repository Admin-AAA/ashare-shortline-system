"""§493 SUEP_h20：上期 SUE≥1 ∩ 本期 SUE≥2（盈余意外持续性）；排序 min(SUE,20)。预注册 docs §493。"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_data as L
c = pl.col
NAME, HOLD, K, MAX_NEW = "SUEP_h20", 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def suep_events(td):
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            sch = pl.read_parquet_schema(f)
            if not {"ts_code", "ann_date", "end_date", "profit_dedt"} <= set(sch):
                continue
            parts.append(pl.read_parquet(f, columns=["ts_code", "ann_date", "end_date", "profit_dedt"])
                         .with_columns(c("ts_code").cast(pl.Utf8), c("ann_date").cast(pl.Utf8), c("end_date").cast(pl.Utf8),
                                       c("profit_dedt").cast(pl.Float64, strict=False)))
        except Exception:
            continue
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date"])
    d = d.filter(c("end_date").str.slice(4, 4).is_in(["0331", "0630", "0930", "1231"]))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first")
    d = d.with_columns((c("end_date").str.slice(0, 4).cast(pl.Int32) * 4 + c("end_date").str.slice(4, 2).cast(pl.Int32) // 3).alias("qi"),
                       (c("end_date").str.slice(4, 2).cast(pl.Int32) // 3).alias("qn"))
    prv = d.select("ts_code", (c("qi") + 1).alias("qi"), c("profit_dedt").alias("pd_prev"))
    d = d.join(prv, on=["ts_code", "qi"], how="left")
    d = d.with_columns(pl.when(c("qn") == 1).then(c("profit_dedt")).otherwise(c("profit_dedt") - c("pd_prev")).alias("q"))
    ly = d.select("ts_code", (c("qi") + 4).alias("qi"), c("q").alias("q_ly"))
    d = d.join(ly, on=["ts_code", "qi"], how="left").with_columns((c("q") - c("q_ly")).alias("ue")).sort("ts_code", "qi")
    d = d.with_columns(c("ue").shift(1).rolling_std(8, min_samples=6).over("ts_code").alias("sig"))
    d = d.with_columns(pl.when(c("sig") > 0).then(c("ue") / c("sig")).alias("sue"))
    d = d.with_columns(c("sue").shift(1).over("ts_code").alias("sue_prev"))
    d = _pit(d, td)
    return (d.sort("end_date").group_by("code", "sd")
             .agg(c("sue").last(), c("sue_prev").last(), c("q").last().alias("q_sq"), c("end_date").last().alias("sue_ed"))
             .rename({"sd": "trade_date"}))


def prep():
    df = build()
    td = df.select("trade_date").unique().sort("trade_date")
    df = df.join(suep_events(td), on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    S = c("liq") & (c("sue") >= 2) & (c("q_sq") > 0) & (c("sue_prev") >= 1)
    return df.with_columns(
        S.fill_null(False).alias("SUEP"),
        (c("sue").clip(-1e9, 20).fill_null(-1e9) - tie).alias("sc_suep"),
    ).sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0
        mx = max(mx, k)
    return mx


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    print("SUEP/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(c("SUEP").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        t, cur = run_real(df, "SUEP", "sc_suep", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
