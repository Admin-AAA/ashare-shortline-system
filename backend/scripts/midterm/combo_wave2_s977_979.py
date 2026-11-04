"""§977/§978/§979 同样本换 combo 排序（预注册 docs §977–§979）。用法：python combo_wave2_s977_979.py <FINAVOL|EACASVOL|RPU20> "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
HOLD, K, MAX_NEW = 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
TIE = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
VOL = c("amount").is_not_null() & c("amt20").is_not_null() & (c("amount") >= 1.5 * c("amt20"))


def rp(sig, expr):
    return pl.when(c(sig)).then(expr).rank().over("trade_date") / pl.when(c(sig)).then(1).sum().over("trade_date")


def prep(which):
    if which == "FINAVOL":
        import lt_research70 as R
        df = R.prep().with_columns((c("FINA") & VOL).fill_null(False).alias("SIG"), (c("dq").fill_null(0) - TIE).alias("sc_base"))
        combo = rp("SIG", c("pct_chg")).fill_null(0) + rp("SIG", -c("total_mv")).fill_null(0)
    elif which == "EACASVOL":
        import lt_research122 as S122, lt_research89 as R89
        df = S122.prep()
        cas = R89.prep().select(["code", "trade_date", c("CAS").alias("CAS")])
        df = df.join(cas, on=["code", "trade_date"], how="left").with_columns(
            (c("G") & c("CAS").fill_null(False) & VOL).fill_null(False).alias("SIG"), (c("sc_g") - TIE).alias("sc_base"))
        combo = rp("SIG", c("pct_chg")).fill_null(0) + rp("SIG", -c("total_mv")).fill_null(0)
    elif which == "RPU20":
        import lt_research23 as R23
        df = R23.prep().sort("code", "trade_date")
        band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
        ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
        muted = (c("pct_chg") >= -1.0) & (c("pct_chg") <= 2.0) & ~(c("close") >= ul - 0.005).fill_null(False)
        df = df.with_columns((c("RP_r05") & muted).fill_null(False).alias("SIG"), (-c("pct_chg").fill_null(0) - TIE).alias("sc_base"))
        combo = rp("SIG", -c("pct_chg")).fill_null(0) + rp("SIG", -c("total_mv")).fill_null(0)
    else:
        raise SystemExit(which)
    return df.with_columns((combo - TIE).alias("sc_combo")).sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0
        mx = max(mx, k)
    return mx


def one(df, sc, name, ph, s, e, out, res):
    t, cur = run_real(df, "SIG", sc, fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
    m = full_metrics(df, t, cur)
    if t is not None and t.height:
        m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
        t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{name}_{ph.split()[0]}.csv"))
    res.setdefault(name, {})[ph] = m
    print(name, ph, m, flush=True)


if __name__ == "__main__":
    which, phases, out = sys.argv[1], sys.argv[2].split(","), sys.argv[3]
    df = prep(which)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        one(df, "sc_base", f"{which}_base", ph, s, e, out, res)
        one(df, "sc_combo", f"{which}_combo_h20", ph, s, e, out, res)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
