"""§785 BTTADVOL_h20：BT ∩ TAD ∩ 放量；排序 prem。预注册 docs §785。"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_research85 as R85
c = pl.col
NAME, HOLD, K, MAX_NEW = "BTTADVOL_h20", 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def prep():
    df = R25.prep()
    tad = R85.prep().select(["code", "trade_date", c("TAD").alias("TAD")])
    df = df.join(tad, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    # 现役 mid-block-trade-premium：ratio≥0.45%、prem≥1%（§474/§498）
    bt = c("liq") & c("bt_amt").is_not_null() & (c("bt_ratio") >= 0.0045) & (c("bt_prem") >= 0.01)
    vol = c("amount").is_not_null() & c("amt20").is_not_null() & (c("amount") >= 1.5 * c("amt20"))
    return df.with_columns(
        (bt & c("TAD").fill_null(False) & vol).fill_null(False).alias("BTTADVOL"),
        (c("bt_prem").fill_null(0) - tie).alias("sc_bttadvol"),
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
    print("BTTADVOL/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(
        c("BTTADVOL").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        t, cur = run_real(df, "BTTADVOL", "sc_bttadvol", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
