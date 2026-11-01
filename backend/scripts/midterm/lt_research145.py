"""§369 EAU_h20：现役业绩加速(G) + 公告日弱反应，持20日。预注册 docs §369。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research145.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
import lt_research20 as R20
c = pl.col
NAME, HOLD, K, MAX_NEW, DEDUP_D = "EAU_h20", 20, 20, 5, 60
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def prep():
    df = R20.prep().sort("code", "trade_date")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
    df = df.with_columns(
        (c("close") >= ul - 0.005).fill_null(False).alias("luc"),
        c("pct_chg").alias("pct"),
    )
    ev = df.filter(c("G")).select("code", "trade_date", "sc_g").with_columns(c("trade_date").str.to_date().alias("d")).sort("code", "d")
    ev = ev.with_columns(c("d").shift(1).over("code").alias("pd"))
    ev = ev.filter(c("pd").is_null() | ((c("d") - c("pd")).dt.total_days() > DEDUP_D))
    ev = ev.select("code", "trade_date", pl.lit(True).alias("ev_ok"))
    df = df.join(ev, on=["code", "trade_date"], how="left")
    muted = (c("pct") >= -1.0) & (c("pct") <= 2.0) & ~c("luc")
    cand = (c("ev_ok").fill_null(False) & c("liq") & muted).fill_null(False)
    df = df.with_columns(cand.alias("cand"))
    df = df.with_columns(pl.when(c("cand")).then(-c("sc_g")).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns(
        (c("cand") & (c("_rk") <= MAX_NEW)).fill_null(False).alias("EAU"),
        pl.when(c("cand")).then(c("sc_g")).otherwise(-1e9).alias("sc_eau"),
    ).drop("_rk").sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0
        mx = max(mx, k)
    return mx


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    print("EAU per year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(c("EAU").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    outd = os.path.dirname(out) or "."
    for ph in phases:
        s, e = PH[ph]
        w = df.filter(c("EAU") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"n_sig": w.height}
        if w.height:
            info.update({"sigdays": w["trade_date"].n_unique(), "pct_med": round(float(w["pct"].median()), 2)})
        res.setdefault("_info", {})[ph] = info
        print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "EAU", "sc_eau", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t))
            m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(outd, f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(outd, exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
