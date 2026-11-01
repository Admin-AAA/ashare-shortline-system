"""§400 F_sc_np_h20：现役预告预喜事件不变，排序键增幅→净利下限。同进程 F_base。预注册 docs §400。"""
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
    anyp = c("type").is_in(["预增", "扭亏", "略增"]) & (c("p_change_min") > 0) & (c("net_profit_min") > 0)
    d = d.with_columns(
        anyp.fill_null(False).alias("fa"),
        c("p_change_min").clip(-1e9, 300).alias("fsc"),
        c("net_profit_min").alias("np"),
    ).filter(c("fa"))
    d = _pit(d, td)
    ev = d.group_by("code", "sd").agg(
        c("fsc").max().alias("sc_fa"),
        c("np").max().alias("sc_np"),
    ).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    return df.with_columns(
        (c("liq") & c("sc_fa").is_not_null()).fill_null(False).alias("F_any2"),
        (c("sc_fa").fill_null(0) - tie).alias("sc_fa"),
        (c("sc_np").fill_null(0) - tie).alias("sc_np"),
    ).sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0
        mx = max(mx, k)
    return mx


def one(df, sc, name, ph, s, e, out, res):
    t, cur = run_real(df, "F_any2", sc, fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
    m = full_metrics(df, t, cur)
    if t is not None and t.height:
        m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
        t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{name}_{ph.split()[0]}.csv"))
    res.setdefault(name, {})[ph] = m
    print(name, ph, m, flush=True)


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    print("FA/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(c("F_any2").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        one(df, "sc_fa", "F_base", ph, s, e, out, res)
        one(df, "sc_np", "F_sc_np_h20", ph, s, e, out, res)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
