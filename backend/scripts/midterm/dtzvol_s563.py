"""§563 DTZVOL_h20：扣非扭亏 ∩ 放量≥1.5×amt20；排序扣非规模封顶300亿。预注册 docs §563。"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research12 import _pit
import lt_data as L
c = pl.col
NAME, HOLD, K, MAX_NEW = "DTZVOL_h20", 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
COLS = ["ts_code", "ann_date", "end_date", "profit_dedt"]


def fina_turn():
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=COLS)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            c("profit_dedt").cast(pl.Float64, strict=False),
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "profit_dedt"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    prev = d.select(
        "ts_code",
        ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
        c("profit_dedt").alias("prev_pdd"),
    )
    d = d.join(prev, on=["ts_code", "end_date"], how="left")
    d = d.filter((c("profit_dedt") > 0) & (c("prev_pdd") < 0))
    d = d.with_columns(c("ts_code").str.slice(0, 6).alias("code"))
    return d


def prep():
    df = build().sort("code", "trade_date")
    td = df.select("trade_date").unique().sort("trade_date")
    ev = _pit(fina_turn(), td)
    ev = ev.group_by("code", "sd").agg(c("profit_dedt").max().alias("pdd")).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    vol = c("amount").is_not_null() & c("amt20").is_not_null() & (c("amount") >= 1.5 * c("amt20"))
    return df.with_columns(
        (c("liq") & c("pdd").is_not_null() & vol).fill_null(False).alias("DTZVOL"),
        ((c("pdd") / 1e8).clip(-1e9, 300).fill_null(0) - tie).alias("sc_dtzvol"),
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
    print("DTZVOL/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(c("DTZVOL").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        t, cur = run_real(df, "DTZVOL", "sc_dtzvol", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
