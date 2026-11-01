"""§477 INBU20_h20：大额增持≥0.5% ∩ 弱反应；持20。预注册 docs §477。"""
import sys, json, os, glob
from datetime import timedelta
import polars as pl
import lt_data as L
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
NAME, HOLD, K, MAX_NEW, CR_MIN, DEDUP_D = "INBU20_h20", 20, 20, 5, 0.5, 20
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def in_events():
    h = pl.concat([pl.read_parquet(f, columns=["ts_code", "ann_date", "change_ratio"])
                   for f in sorted(glob.glob(str(L.D / "events" / "holdertrade_in_*.parquet")))], how="diagonal_relaxed")
    h = h.filter(c("ann_date").str.len_chars() == 8).with_columns(
        c("ts_code").str.slice(0, 6).alias("code"), c("ann_date").str.to_date("%Y%m%d").alias("a"),
        c("change_ratio").cast(pl.Float64, strict=False).fill_null(0)).unique()
    g = h.group_by("code", "a").agg(c("change_ratio").sum().alias("cr")).sort("code", "a")
    rows = []
    for (code,), x in g.partition_by("code", as_dict=True).items():
        last = None
        for a, cr in zip(x["a"].to_list(), x["cr"].to_list()):
            if last is not None and (a - last).days <= DEDUP_D:
                continue
            last = a
            if cr >= CR_MIN:
                rows.append((code, a, float(cr)))
    return pl.DataFrame(rows, schema=["code", "a", "cr"], orient="row")


def prep():
    df = build().sort("code", "trade_date")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
    muted = (c("pct_chg") >= -1.0) & (c("pct_chg") <= 2.0) & ~(c("close") >= ul - 0.005).fill_null(False)
    ev = in_events().with_columns((c("a") + timedelta(days=1)).alias("a1"))
    td = df.select("trade_date").unique().with_columns(c("trade_date").str.to_date().alias("sdd")).sort("sdd")
    ev = ev.sort("a1").join_asof(td, left_on="a1", right_on="sdd", strategy="forward").drop_nulls("trade_date")
    ev = ev.group_by("code", "trade_date").agg(c("cr").max())
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    return df.with_columns(
        (c("cr").is_not_null() & c("liq") & muted).fill_null(False).alias("INBU20"),
        (c("cr").fill_null(0) - tie).alias("sc_cr"),
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
    print("INBU20/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(c("INBU20").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        t, cur = run_real(df, "INBU20", "sc_cr", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
