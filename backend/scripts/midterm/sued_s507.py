"""§507 SUED_h20：SUE≥2 ∩ 扣非同比≥50（无营收/加速要求）；排序 min(SUE,20)。预注册 docs §507。"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
import lt_research122 as S122
c = pl.col
NAME, HOLD, K, MAX_NEW = "SUED_h20", 20, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def prep():
    df = S122.prep()  # 含 sue/q_sq/dtp/G/lim
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    # 比 SUEG 宽：只要扣非同比≥50 + 扣非>0，不要 orv/加速
    sig = c("liq") & (c("sue") >= 2) & (c("q_sq") > 0) & (c("dtp") >= 50) & (c("pdd") > 0)
    return df.with_columns(
        sig.fill_null(False).alias("SUED"),
        (c("sue").clip(-1e9, 20).fill_null(-1e9) - tie).alias("sc_sued"),
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
    print("SUED/SUEG/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(
        c("SUED").sum(), (c("SUE") & c("G")).sum().alias("SUEG")).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        t, cur = run_real(df, "SUED", "sc_sued", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
