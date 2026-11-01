"""§653 EPSVOL_h20：基本每股收益同比 ∩ 放量≥1.5×amt20；排序 sc_eps。预注册 docs §653。"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
NAME, HOLD, K, MAX_NEW = "EPSVOL_h20", 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
EPS_MIN, EPS_CAP = 20.0, 100.0


def eps_events(td):
    cols = ["ts_code", "ann_date", "end_date", "basic_eps_yoy", "netprofit_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=cols)
        except Exception:
            continue
        parts.append(x.with_columns(
            *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")],
            *[c(k).cast(pl.Float64, strict=False) for k in ("basic_eps_yoy", "netprofit_yoy")],
        ))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date", "basic_eps_yoy"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    d = d.filter(c("basic_eps_yoy") >= EPS_MIN)
    ev = _pit(d, td)
    return ev.group_by("code", "sd").agg(c("basic_eps_yoy").max().alias("eps_yoy")).rename({"sd": "trade_date"})


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    df = df.join(eps_events(td), on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    vol = c("amount").is_not_null() & c("amt20").is_not_null() & (c("amount") >= 1.5 * c("amt20"))
    sig = c("liq") & c("eps_yoy").is_not_null()
    return df.with_columns(
        (sig & vol).fill_null(False).alias("EPSVOL"),
        (c("eps_yoy").clip(-1e9, EPS_CAP).fill_null(0) - tie).alias("sc_epsvol"),
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
    print("EPSVOL/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(c("EPSVOL").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        t, cur = run_real(df, "EPSVOL", "sc_epsvol", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
