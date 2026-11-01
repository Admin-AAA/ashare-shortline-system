"""§698 FINAVOL_sc_rel_h20：财务费用∩放量同样本改按相对降幅排序，同进程绝对 dq base。预注册 docs §698。"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
import lt_research70 as R70
c = pl.col
HOLD, K, MAX_NEW = 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def prep():
    df = R70.prep()
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    vol = c("amount").is_not_null() & c("amt20").is_not_null() & (c("amount") >= 1.5 * c("amt20"))
    sig = (c("FINA") & vol).fill_null(False)
    # prev ≈ finaexp + dq；相对降幅 dq/prev（与 §416 一致）
    prev = c("finaexp_of_gr") + c("dq")
    rel = pl.when(prev.is_not_null() & (prev.abs() > 1e-9)).then(c("dq") / prev).otherwise(pl.lit(0.0))
    return df.with_columns(
        sig.alias("FINAVOL"),
        (c("dq").fill_null(0) - tie).alias("sc_raw"),
        (rel.fill_null(0) - tie).alias("sc_rel"),
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
    print("FINAVOL/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(c("FINAVOL").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        one(df, "FINAVOL", "sc_raw", "FINAVOL_base", ph, s, e, out, res)
        one(df, "FINAVOL", "sc_rel", "FINAVOL_sc_rel_h20", ph, s, e, out, res)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
