"""§771 FLZCASVOL_h20：略增 ∩ CAS ∩ 放量；排序 net_profit_min。预注册 docs §771。"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
from lt_research20 import forecast_first, prep as prep20
import lt_research89 as R89
c = pl.col
NAME, HOLD, K, MAX_NEW = "FLZCASVOL_h20", 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def prep():
    df = prep20()
    dates = df.select("trade_date").unique().sort("trade_date")
    d = forecast_first()
    ok = (c("type") == "略增") & (c("p_change_min") > 0) & (c("net_profit_min") > 0)
    d = d.with_columns(ok.fill_null(False).alias("lz"), c("net_profit_min").alias("np")).filter(c("lz"))
    d = _pit(d, dates)
    ev = d.group_by("code", "sd").agg(c("np").max().alias("np")).rename({"sd": "trade_date"})
    cas = R89.prep().select(["code", "trade_date", c("CAS").alias("CAS")])
    df = df.join(ev, on=["code", "trade_date"], how="left").join(cas, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    vol = c("amount").is_not_null() & c("amt20").is_not_null() & (c("amount") >= 1.5 * c("amt20"))
    return df.with_columns(
        (c("liq") & c("np").is_not_null() & c("CAS").fill_null(False) & vol).fill_null(False).alias("FLZCASVOL"),
        (c("np").fill_null(0) - tie).alias("sc_flzcasvol"),
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
    print("FLZCASVOL/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(
        c("FLZCASVOL").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        t, cur = run_real(df, "FLZCASVOL", "sc_flzcasvol", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
