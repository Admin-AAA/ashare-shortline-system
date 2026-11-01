"""§449 FTZU_h20：预告扭亏 + 信号日弱反应。预注册 docs §449。"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research12 import _pit
from lt_research20 import forecast_first, prep as prep20
c = pl.col
NAME, HOLD, K, MAX_NEW = "FTZU_h20", 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def prep():
    df = prep20().sort("code", "trade_date")
    td = df.select("trade_date").unique().sort("trade_date")
    d = forecast_first()
    d = d.with_columns(
        (c("type") == "扭亏").fill_null(False).alias("tz"),
        c("net_profit_min").alias("np"),
        c("p_change_min").alias("pcm"),
    ).filter(c("tz") & (c("np") > 0))
    d = _pit(d, td)
    ev = d.group_by("code", "sd").agg(
        c("np").max().alias("np"),
        c("pcm").max().alias("pcm"),
    ).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
    sc_pcm = pl.coalesce(c("pcm"), c("np") / 1e6)
    muted = (c("pct_chg") >= -1.0) & (c("pct_chg") <= 2.0) & ~(c("close") >= ul - 0.005).fill_null(False)
    ftz = c("liq") & c("np").is_not_null()
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    return df.with_columns(
        (ftz & muted).fill_null(False).alias("FTZU"),
        (sc_pcm.fill_null(0) - tie).alias("sc_u"),
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
    print("FTZU/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(c("FTZU").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        t, cur = run_real(df, "FTZU", "sc_u", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
