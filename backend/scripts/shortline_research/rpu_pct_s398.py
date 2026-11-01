"""[短线研究 §398] RPU 同样本改按 −pct 排序。同进程 RPU_base。预注册 docs §398。"""
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
    ev = df.filter(c("RP_r05")).select("code", "trade_date", "sc_r").with_columns(c("trade_date").str.to_date().alias("d")).sort("code", "d")
    ev = ev.with_columns(c("d").shift(1).over("code").alias("pd"))
    ev = ev.filter(c("pd").is_null() | ((c("d") - c("pd")).dt.total_days() > DEDUP_D))
    ev = ev.select("code", "trade_date", pl.lit(True).alias("ev_ok"))
    df = df.join(ev, on=["code", "trade_date"], how="left")
    muted = (c("pct") >= -1.0) & (c("pct") <= 2.0) & ~c("luc")
    cand = (c("ev_ok").fill_null(False) & c("liq") & muted).fill_null(False)
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    df = df.with_columns(
        cand.alias("RPU_pool"),
        pl.when(cand).then(c("sc_r") - tie).otherwise(-1e9).alias("sc_rpu"),
        pl.when(cand).then(-c("pct").fill_null(0) - tie).otherwise(-1e9).alias("sc_pct"),
    )
    return df.sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0
        mx = max(mx, k)
    return mx


def one(df, sc, name, ph, s, e, out, res):
    t, cur = run_real(df, "RPU_pool", sc, fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
    m = full_metrics(df, t, cur)
    if t is not None and t.height:
        m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
        t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{name}_{ph.split()[0]}.csv"))
    res.setdefault(name, {})[ph] = m
    print(name, ph, m, flush=True)


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    print("RPU_pool/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(c("RPU_pool").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        one(df, "sc_rpu", "RPU_base", ph, s, e, out, res)
        one(df, "sc_pct", "RPU_sc_pct_h5", ph, s, e, out, res)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
