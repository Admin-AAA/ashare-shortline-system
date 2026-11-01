"""[短线研究 §372] 现役 RPU 提高回购金额门槛≥1%（RPU_r1_h5）。
预注册 docs §372。同 RPU 弱反应，另加 sc_r≥0.01；同进程跑 RPU_base 作对照。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/rpu_r1_s372.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research23 import prep as prep23
c = pl.col
HOLD, K, MAX_NEW, DEDUP_D = 5, 20, 5, 60
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def prep():
    df = prep23().sort("code", "trade_date")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
    df = df.with_columns(
        (c("close") >= ul - 0.005).fill_null(False).alias("luc"),
        c("pct_chg").alias("pct"),
    )
    muted = (c("pct") >= -1.0) & (c("pct") <= 2.0) & ~c("luc")

    def pick(ev_col, sc_col, out_sig, out_sc, thr=None):
        nonlocal df
        f = c(ev_col)
        if thr is not None:
            f = f & (c(sc_col) >= thr)
        ev = df.filter(f).select("code", "trade_date", sc_col).with_columns(c("trade_date").str.to_date().alias("d")).sort("code", "d")
        ev = ev.with_columns(c("d").shift(1).over("code").alias("pd"))
        ev = ev.filter(c("pd").is_null() | ((c("d") - c("pd")).dt.total_days() > DEDUP_D))
        ev = ev.select("code", "trade_date", pl.lit(True).alias("_ok"))
        x = df.join(ev, on=["code", "trade_date"], how="left")
        cand = (c("_ok").fill_null(False) & c("liq") & muted).fill_null(False)
        x = x.with_columns(cand.alias("_cand"))
        x = x.with_columns(pl.when(c("_cand")).then(-c(sc_col)).rank("ordinal").over("trade_date").alias("_rk"))
        df = x.with_columns(
            (c("_cand") & (c("_rk") <= MAX_NEW)).fill_null(False).alias(out_sig),
            pl.when(c("_cand")).then(c(sc_col)).otherwise(-1e9).alias(out_sc),
        ).drop(["_ok", "_cand", "_rk"])
        return df

    df = pick("RP_r05", "sc_r", "RPU", "sc_rpu", thr=None)
    df = pick("RP_r05", "sc_r", "RPU1", "sc_rpu1", thr=0.01)
    return df.sort("code", "trade_date")


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
        m["streak"] = float(streak(t))
        m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
        t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{name}_{ph.split()[0]}.csv"))
    res.setdefault(name, {})[ph] = m
    print(name, ph, m, flush=True)


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        one(df, "RPU", "sc_rpu", "RPU_base", ph, s, e, out, res)
        one(df, "RPU1", "sc_rpu1", "RPU_r1_h5", ph, s, e, out, res)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
