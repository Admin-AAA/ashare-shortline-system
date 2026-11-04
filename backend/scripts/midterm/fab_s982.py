"""§982 FAB_h20：正式财报归母净利同比超出业绩预告上限 ≥10pp。预注册 docs §982。"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research12 import _pit
import lt_research20 as R20
import lt_data as L
c = pl.col
NAME, HOLD, K, MAX_NEW, BEAT, CAP = "FAB_h20", 20, 20, 5, 10.0, 100.0
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def fina_first():
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=["ts_code", "ann_date", "end_date", "netprofit_yoy"])
        except Exception:
            continue
        if x.height:
            parts.append(x.with_columns(c("ts_code").cast(pl.Utf8), c("ann_date").cast(pl.Utf8).str.strip_chars(),
                                        c("end_date").cast(pl.Utf8), c("netprofit_yoy").cast(pl.Float64, strict=False)))
    d = pl.concat(parts, how="vertical_relaxed").filter(c("ann_date").str.len_chars() == 8)
    return d.sort("ann_date").unique(["ts_code", "end_date"], keep="first", maintain_order=True)


def fab_events(td):
    fc = R20.forecast_first().filter(c("type").is_in(["预增", "略增"]) & c("p_change_max").is_not_null()) \
        .select("ts_code", "end_date", c("ann_date").alias("fc_ann"), "p_change_max")
    fi = fina_first().join(fc, on=["ts_code", "end_date"], how="inner").filter(c("fc_ann") < c("ann_date"))
    fi = fi.with_columns((c("netprofit_yoy") - c("p_change_max")).alias("beat")) \
        .filter((c("netprofit_yoy") > 0) & (c("beat") >= BEAT))
    return _pit(fi, td).group_by("code", "sd").agg(c("beat").max().clip(upper_bound=CAP))


def prep():
    df = R20.build()
    td = df.select("trade_date").unique().sort("trade_date")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    df = df.join(fab_events(td).rename({"sd": "trade_date"}), on=["code", "trade_date"], how="left")
    return df.with_columns((c("liq") & c("beat").is_not_null()).fill_null(False).alias("FAB"),
                           (c("beat").fill_null(0) - tie).alias("sc_fab")).sort("code", "trade_date")


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    print("FAB/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(c("FAB").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        t, cur = run_real(df, "FAB", "sc_fab", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
