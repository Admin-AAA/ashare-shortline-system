"""§514 BT_p0135_h20：大宗溢价硬门槛≥1.35%（ratio 仍≥0.45%），同进程 BT_base(prem≥1.2%,ratio≥0.45%)。预注册 docs §514。"""
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
    base = c("liq") & c("bt_amt").is_not_null() & (c("bt_ratio") >= 0.0045) & (c("bt_prem") >= 0.012)
    hard = c("liq") & c("bt_amt").is_not_null() & (c("bt_ratio") >= 0.0045) & (c("bt_prem") >= 0.0135)
    return df.with_columns(
        base.fill_null(False).alias("BT_base"),
        hard.fill_null(False).alias("BT_p0135"),
        (c("bt_prem").fill_null(0) - tie).alias("sc_prem"),
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
    print("BT_base/BT_p0135/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(c("BT_base").sum(), c("BT_p0135").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        one(df, "BT_base", "sc_prem", "BT_base", ph, s, e, out, res)
        one(df, "BT_p0135", "sc_prem", "BT_p0135_h20", ph, s, e, out, res)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
