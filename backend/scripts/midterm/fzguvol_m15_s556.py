"""§556 FZGUVOL_m15_h20：预增弱反应∩放量 弱反应带软扩[-1.5,+2.5]，同进程 FZGUVOL_base([-1,+2])。预注册 docs §556。"""
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
    df = prep20().sort("code", "trade_date")
    td = df.select("trade_date").unique().sort("trade_date")
    d = forecast_first()
    ok = (c("type") == "预增") & (c("p_change_min") > 0) & (c("net_profit_min") > 0)
    d = d.with_columns(ok.fill_null(False).alias("zg"), c("p_change_min").clip(-1e9, 300).alias("sc")).filter(c("zg"))
    d = _pit(d, td)
    ev = d.group_by("code", "sd").agg(c("sc").max().alias("sc")).rename({"sd": "trade_date"})
    df = df.join(ev, on=["code", "trade_date"], how="left")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
    not_ul = ~(c("close") >= ul - 0.005).fill_null(False)
    muted = (c("pct_chg") >= -1.0) & (c("pct_chg") <= 2.0) & not_ul
    soft = (c("pct_chg") >= -1.5) & (c("pct_chg") <= 2.5) & not_ul
    vol = c("amount").is_not_null() & c("amt20").is_not_null() & (c("amount") >= 1.5 * c("amt20"))
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    fzg = c("liq") & c("sc").is_not_null() & vol
    return df.with_columns(
        (fzg & muted).fill_null(False).alias("FZGUVOL_base"),
        (fzg & soft).fill_null(False).alias("FZGUVOL_m15"),
        (c("sc").fill_null(0) - tie).alias("sc_fzguvol"),
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
    print("FZGUVOL_base/m15/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(c("FZGUVOL_base").sum(), c("FZGUVOL_m15").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        one(df, "FZGUVOL_base", "sc_fzguvol", "FZGUVOL_base", ph, s, e, out, res)
        one(df, "FZGUVOL_m15", "sc_fzguvol", "FZGUVOL_m15_h20", ph, s, e, out, res)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
