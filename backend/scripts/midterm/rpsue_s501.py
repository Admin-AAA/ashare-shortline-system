"""§501 RPSUE_h20：回购预案≥0.5% ∩ SUE≥2；排序 min(SUE,20)。预注册 docs §501。"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
import lt_research23 as R23
import lt_research122 as S122
c = pl.col
NAME, HOLD, K, MAX_NEW = "RPSUE_h20", 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def prep():
    # R23 带齐全行情列（lim_up_open 等）；SUE 列左连接
    df = R23.prep()
    sue = S122.prep().select("code", "trade_date", "sue", "q_sq", "sc_sue")
    df = df.join(sue, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    sig = c("RP_r05").fill_null(False) & (c("sue") >= 2) & (c("q_sq") > 0)
    return df.with_columns(
        sig.fill_null(False).alias("RPSUE"),
        (c("sc_sue").fill_null(-1e9) - tie).alias("sc_rpsue"),
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
    print("RPSUE/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(c("RPSUE").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        t, cur = run_real(df, "RPSUE", "sc_rpsue", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
