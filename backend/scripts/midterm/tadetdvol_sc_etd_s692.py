"""§692 TADETDVOL_sc_etd_h20：同样本改按 ETD 升幅排序，同进程 base(按TAD)。预注册 docs §692。"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
import lt_research85 as R85
import lt_research86 as R86
c = pl.col
HOLD, K, MAX_NEW = 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def prep():
    df = R85.prep()
    etd = R86.prep().select(["code", "trade_date", c("ETD").alias("ETD"), c("delta").alias("etd_delta"), c("sc_etd").alias("sc_etd")])
    df = df.join(etd, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    vol = c("amount").is_not_null() & c("amt20").is_not_null() & (c("amount") >= 1.5 * c("amt20"))
    tad_live = c("TAD") & (c("delta") >= 0.25)
    etd_live = c("ETD").fill_null(False)
    sig = tad_live & etd_live & vol
    return df.with_columns(
        sig.fill_null(False).alias("TADETDVOL"),
        (c("sc_tad") - tie).alias("sc_tad"),
        (c("sc_etd").fill_null(0) - tie).alias("sc_etd_sort"),
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
    print("TADETDVOL/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(c("TADETDVOL").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        one(df, "TADETDVOL", "sc_tad", "TADETDVOL_base", ph, s, e, out, res)
        one(df, "TADETDVOL", "sc_etd_sort", "TADETDVOL_sc_etd_h20", ph, s, e, out, res)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
