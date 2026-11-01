"""§386 SDY_dv3_h20：现役小盘高股息加 dv_ttm≥3 硬门槛。同进程 SDY_base。预注册 docs §386。"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research104 import prep as prep104
c = pl.col
HOLD, K, MAX_NEW = 20, 20, 10
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def prep():
    df = prep104()
    wk = (c("trade_date").str.to_date().dt.weekday() == 5)
    on = c("scid") > c("scid20")
    U0 = ((c("rk_s") <= 400) & (c("dv_ttm") > 0)).fill_null(False)
    U3 = ((c("rk_s") <= 400) & (c("dv_ttm") >= 3)).fill_null(False)
    df = df.with_columns(
        pl.when(U0).then(-c("dv_ttm")).rank("ordinal").over("trade_date").alias("rk_y0"),
        pl.when(U3).then(-c("dv_ttm")).rank("ordinal").over("trade_date").alias("rk_y3"),
        c("dv_ttm").fill_null(-1).alias("sc_dy"),
    )
    return df.with_columns(
        (wk & on & (c("rk_y0") <= 10)).fill_null(False).alias("SDY_base"),
        (wk & on & (c("rk_y3") <= 10)).fill_null(False).alias("SDY_dv3"),
    ).sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0
        mx = max(mx, k)
    return mx


def one(df, sig, name, ph, s, e, out, res):
    t, cur = run_real(df, sig, "sc_dy", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
    m = full_metrics(df, t, cur)
    if t is not None and t.height:
        m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
        t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{name}_{ph.split()[0]}.csv"))
    res.setdefault(name, {})[ph] = m
    print(name, ph, m, flush=True)


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    os.environ.setdefault("LPS_ST_SRC", "db")
    df = prep()
    print("SDY counts/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(
        c("SDY_base").sum(), c("SDY_dv3").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        one(df, "SDY_base", "SDY_base", ph, s, e, out, res)
        one(df, "SDY_dv3", "SDY_dv3_h20", ph, s, e, out, res)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
