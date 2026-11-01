"""§417 FREV10_h20：业绩预告中值上修≥10%。预注册 docs §417。"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research12 import _pit
import lt_data as L
c = pl.col
NAME, HOLD, K, MAX_NEW, UPLIFT = "FREV10_h20", 20, 20, 5, 0.10
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
COLS = ["ts_code", "ann_date", "end_date", "net_profit_min", "net_profit_max"]


def frev_events(td):
    parts = []
    for f in sorted(glob.glob(str(L.D / "forecast" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=COLS)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("net_profit_min", "net_profit_max")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    mid = pl.coalesce(
        (c("net_profit_min") + c("net_profit_max")) / 2,
        c("net_profit_min"),
        c("net_profit_max"),
    )
    d = d.with_columns(mid.alias("mid"), c("ts_code").str.slice(0, 6).alias("code")).drop_nulls("mid")
    d = d.sort("code", "end_date", "ann_date")
    d = d.with_columns(c("mid").shift(1).over("code", "end_date").alias("prev_mid"))
    d = d.with_columns(
        ((c("mid") - c("prev_mid")) / c("prev_mid").abs().clip(1e-6, None)).alias("uplift")
    ).filter(
        c("prev_mid").is_not_null() & (c("mid") > 0) & (c("uplift") >= UPLIFT)
    )
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(c("uplift").max().alias("uplift")).rename({"sd": "trade_date"})


def prep():
    df = build().sort("code", "trade_date")
    td = df.select("trade_date").unique().sort("trade_date")
    ev = frev_events(td)
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    return df.with_columns(
        (c("liq") & c("uplift").is_not_null()).fill_null(False).alias("FREV"),
        (c("uplift").clip(-1e9, 10).fill_null(0) - tie).alias("sc_frev"),
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
    print("FREV/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(c("FREV").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        t, cur = run_real(df, "FREV", "sc_frev", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
