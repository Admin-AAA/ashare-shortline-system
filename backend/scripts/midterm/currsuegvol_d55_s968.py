"""§968 CURRSUEGVOL_d55_h20：G 内 dtp硬抬≥55，同进程 base(dtp≥50)；sc_curr。预注册 docs §968。"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_research122 as S122
import lt_data as L
c = pl.col
HOLD, K, MAX_NEW = 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
FLOOR, CAP = 1.5, 5.0


def curr_events(td):
    cols = ["ts_code", "ann_date", "end_date", "current_ratio"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            c("current_ratio").cast(pl.Float64, strict=False),
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "current_ratio"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    d = d.filter(c("current_ratio") >= FLOOR)
    d = d.with_columns(c("current_ratio").clip(-1e9, CAP).alias("sc_curr"))
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(
        c("current_ratio").max().alias("current_ratio"), c("sc_curr").max().alias("sc_curr"),
    ).rename({"sd": "trade_date"})


def prep():
    df = R25.prep().sort("code", "trade_date")
    td = df.select("trade_date").unique().sort("trade_date")
    df = df.join(curr_events(td), on=["code", "trade_date"], how="left")
    gcols = S122.prep().select(["code", "trade_date", "sue", "q_sq", "pdd", "dtp", "orv", "prev_dtp"])
    drop = [x for x in ("sue", "q_sq", "pdd", "dtp", "orv", "prev_dtp") if x in df.columns]
    if drop:
        df = df.drop(drop)
    df = df.join(gcols, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    vol = c("amount").is_not_null() & c("amt20").is_not_null() & (c("amount") >= 1.5 * c("amt20"))
    sue = c("liq") & (c("sue") >= 2.2) & (c("q_sq") > 0)
    g50 = (c("pdd") > 0) & (c("dtp") >= 50) & (c("orv") >= 20) & (c("dtp") > c("prev_dtp"))
    g55 = (c("pdd") > 0) & (c("dtp") >= 55) & (c("orv") >= 20) & (c("dtp") > c("prev_dtp"))
    cr = c("current_ratio").is_not_null()
    return df.with_columns(
        (cr & sue & g50 & vol).fill_null(False).alias("CURRSUEGVOL_base"),
        (cr & sue & g55 & vol).fill_null(False).alias("CURRSUEGVOL_d55"),
        (c("sc_curr").fill_null(0) - tie).alias("sc_currrsuegvol"),
    ).sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0
        mx = max(mx, k)
    return mx


def one(df, sig, sc, name, ph, s, e, out, res):
    t, cur = run_real(df, sig, sc, fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
    m = full_metrics(df, t, cur)
    if t is not None and t.height:
        m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
        t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{name}_{ph.split()[0]}.csv"))
    res.setdefault(name, {})[ph] = m
    print(name, ph, m, flush=True)


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    print("base/d55/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(
        c("CURRSUEGVOL_base").sum(), c("CURRSUEGVOL_d55").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        one(df, "CURRSUEGVOL_base", "sc_currrsuegvol", "CURRSUEGVOL_base", ph, s, e, out, res)
        one(df, "CURRSUEGVOL_d55", "sc_currrsuegvol", "CURRSUEGVOL_d55_h20", ph, s, e, out, res)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
