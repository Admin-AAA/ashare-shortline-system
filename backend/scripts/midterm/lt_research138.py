"""§355 BT_prem_h20 vs BT_inst_h20（当日至少一笔 buyer 含「机构专用」）。预注册见 docs §355。"""
import sys, json, os, glob
from pathlib import Path
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
import lt_research25 as R25
c = pl.col
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def inst_days(start):
    fs = sorted(glob.glob(str(R25.BT / "*.parquet")))
    if start: fs = [f for f in fs if Path(f).stem >= start.replace("-", "")]
    parts = []
    for f in fs:
        x = pl.read_parquet(f)
        if x.height == 0 or "buyer" not in x.columns: continue
        parts.append(x.select(c("ts_code").cast(pl.Utf8), c("trade_date").cast(pl.Utf8), c("buyer").cast(pl.Utf8)))
    d = pl.concat(parts, how="vertical_relaxed").filter(c("buyer").str.contains("机构专用"))
    d = d.with_columns(c("ts_code").str.slice(0, 6).alias("code"),
                       pl.concat_str([c("trade_date").str.slice(0, 4), c("trade_date").str.slice(4, 2), c("trade_date").str.slice(6, 2)], separator="-").alias("trade_date"))
    return d.select("code", "trade_date").unique().with_columns(pl.lit(True).alias("inst_buy"))


def prep():
    df = R25.prep()
    df = df.join(inst_days(df["trade_date"].min()), on=["code", "trade_date"], how="left")
    return df.with_columns((c("BTP") & c("inst_buy").fill_null(False)).alias("BTI")).sort("code", "trade_date")


RUNS = (("BT_prem_h20", "BTP"), ("BT_inst_h20", "BTI"))
if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    print("BTP/BTI per year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(c("BTP").sum(), c("BTI").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        for n, sig in RUNS:
            t, cur = run_real(df, sig, "sc_bt", fixed(20), s, e, k_slots=20, max_new=5, max_hold=20)
            m = full_metrics(df, t, cur)
            if t is not None and t.height:
                t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{n}_{ph.split()[0]}.csv"))
            res.setdefault(n, {})[ph] = m
            print(n, ph, m, flush=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
