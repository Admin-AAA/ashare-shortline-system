"""§384 BT_p1_h20：现役大宗溢价 bt_prem 门槛 0→1%。同进程 BT_base 对照。预注册 docs §384。"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
import lt_research25 as R25
c = pl.col
HOLD, K, MAX_NEW = 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def prep():
    df = R25.prep()
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    base = c("liq") & c("bt_amt").is_not_null() & (c("bt_prem") >= 0) & (c("bt_ratio") >= 0.005)
    p1 = c("liq") & c("bt_amt").is_not_null() & (c("bt_prem") >= 0.01) & (c("bt_ratio") >= 0.005)
    return df.with_columns(
        base.fill_null(False).alias("BT_base"),
        p1.fill_null(False).alias("BT_p1"),
        (c("bt_ratio").fill_null(0) - tie).alias("sc_bt"),
    ).sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0
        mx = max(mx, k)
    return mx


def one(df, sig, name, ph, s, e, out, res):
    t, cur = run_real(df, sig, "sc_bt", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
    m = full_metrics(df, t, cur)
    if t is not None and t.height:
        m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
        t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{name}_{ph.split()[0]}.csv"))
    res.setdefault(name, {})[ph] = m
    print(name, ph, m, flush=True)


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    print("BTP counts/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(
        c("BT_base").sum(), c("BT_p1").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        one(df, "BT_base", "BT_base", ph, s, e, out, res)
        one(df, "BT_p1", "BT_p1_h20", ph, s, e, out, res)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
