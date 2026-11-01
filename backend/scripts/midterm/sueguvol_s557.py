"""§557 SUEGUVOL_h20：SUEG ∩ 弱反应 ∩ 放量≥1.5×amt20；排序 min(SUE,20)。预注册 docs §557。"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
import lt_research122 as S122
c = pl.col
NAME, HOLD, K, MAX_NEW = "SUEGUVOL_h20", 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def prep():
    df = S122.prep().sort("code", "trade_date")
    cols = set(df.columns)
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    if "up_limit" in cols:
        ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
    else:
        ul = c("close").shift(1).over("code") * (1 + band)
    muted = (c("pct_chg") >= -1.0) & (c("pct_chg") <= 2.0) & ~(c("close") >= ul - 0.005).fill_null(False)
    vol = c("amount").is_not_null() & c("amt20").is_not_null() & (c("amount") >= 1.5 * c("amt20"))
    return df.with_columns(
        (c("SUE") & c("G") & muted & vol).fill_null(False).alias("SUEGUVOL"),
        (c("sc_sue") - tie).alias("sc_sueguvol"),
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
    print("SUEG/SUEGUVOL/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(
        (c("SUE") & c("G")).sum().alias("SUEG"), c("SUEGUVOL").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        t, cur = run_real(df, "SUEGUVOL", "sc_sueguvol", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
