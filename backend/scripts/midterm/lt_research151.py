"""§380 F_nt_h20：现役预告预喜去掉略增噪音。同进程 F_any base。预注册 docs §380。"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research12 import _pit
from lt_research20 import forecast_first, prep as prep20
c = pl.col
HOLD, K, MAX_NEW = 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def prep():
    df = prep20()
    td = df.select("trade_date").unique().sort("trade_date")
    d = forecast_first()
    nt = c("type").is_in(["预增", "扭亏"]) & (c("p_change_min") > 0) & (c("net_profit_min") > 0)
    d = d.with_columns(nt.fill_null(False).alias("fn"), c("p_change_min").clip(-1e9, 300).alias("fsc"))
    d = d.filter(c("fn"))
    d = _pit(d, td)
    ev = d.group_by("code", "sd").agg(c("fsc").max().alias("sc_n")).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    return df.with_columns(
        (c("liq") & c("sc_n").is_not_null()).fill_null(False).alias("F_nt"),
        (c("sc_n").fill_null(0) - tie).alias("sc_nt"),
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
    print("FA/F_nt per year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(
        c("FA").sum(), c("F_nt").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        one(df, "FA", "sc_a", "F_any_base", ph, s, e, out, res)
        one(df, "F_nt", "sc_nt", "F_nt_h20", ph, s, e, out, res)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
