"""[短线研究 §202] 连续安静日后方向性收阳 Consecutive Quiet then Direction（预注册 docs/strategy-research.md §202，结果出来前写定，不调参）。
行情 = midterm 长面板 + realistic 引擎；只用本地数据。
用法：cd backend/scripts/midterm && PYTHONPATH=../..:. ../../.venv/bin/python ../shortline_research/cqd_s202.py ../../output/research/s202/res.json"""
import sys, json
from pathlib import Path
import numpy as np
import polars as pl
import lt_data as L
from realistic import add_limits, run_real
from lt_research import PH

c = pl.col
MAX_NEW, K, HOLD, STOP = 3, 15, 3, 0.93
RET_Q, RNG_Q, RET_UP, CLV_MIN, RET_CAP = 0.01, 0.025, 0.01, 0.70, 0.04
SCORE_CAP = 0.05
PHASES = dict(PH)
PHASES["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
START, END = "2019-01-01", "2026-10-16"


def panel():
    df = L.prices(START, END)
    df = df.with_columns(
        ((1 + c("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code")).alias("ac"),
        pl.int_range(pl.len()).over("code").alias("age"),
    )
    kf = c("ac") / c("close")
    df = df.with_columns(
        (c("open") * kf).alias("ao"),
        c("close").shift(1).over("code").alias("pre_c"),
    )
    df = df.with_columns(
        pl.when(c("pre_c") > 0).then(c("close") / c("pre_c") - 1.0).otherwise(None).alias("day_ret"),
        pl.when(c("pre_c") > 0).then((c("high") - c("low")) / c("pre_c")).otherwise(None).alias("range_pct"),
        pl.when((c("high") - c("low")) > 1e-8)
        .then((c("close") - c("low")) / (c("high") - c("low")))
        .otherwise(None)
        .alias("clv"),
    )
    quiet = (c("day_ret").abs() <= RET_Q) & (c("range_pct") <= RNG_Q)
    df = df.with_columns(quiet.fill_null(False).alias("quiet"))
    # prior consecutive quiet counts via shifts
    q1 = c("quiet").shift(1).over("code")
    q2 = c("quiet").shift(2).over("code")
    q3 = c("quiet").shift(3).over("code")
    df = df.with_columns(q1.alias("q1"), q2.alias("q2"), q3.alias("q3"))
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.sort("trade_date").join_asof(st, on="trade_date", by="code", strategy="backward").with_columns(c("st").fill_null(False))
    df = add_limits(df.sort("code", "trade_date"))
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    up_lim = pl.coalesce(c("up_limit"), c("pre_c") * (1 + band))
    not_ow_up = ~((c("low") >= up_lim - 0.005).fill_null(False))
    not_lock_dn = ~c("lock_down").fill_null(False)
    uni = (
        (~c("st"))
        & (c("age") >= 120)
        & (c("amount") >= 1e8)
        & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
        & c("pre_c").is_not_null()
        & (c("pre_c") > 0)
        & (c("open") > 0)
        & c("day_ret").is_not_null()
        & c("clv").is_not_null()
    )
    base = uni & not_ow_up & not_lock_dn
    up_day = (c("day_ret") >= RET_UP) & (c("clv") >= CLV_MIN)
    core3 = c("q1").fill_null(False) & c("q2").fill_null(False) & c("q3").fill_null(False) & up_day
    core2 = c("q1").fill_null(False) & c("q2").fill_null(False) & up_day
    v1 = base & core3
    v2 = base & core2
    v3 = v1 & (c("day_ret") <= RET_CAP)
    ctrl = base & ~core3.fill_null(False)
    score = (c("day_ret") * c("clv")).clip(0, SCORE_CAP)
    df = df.with_columns(
        v1.fill_null(False).alias("sig_V1_CQD3"),
        v2.fill_null(False).alias("sig_V2_CQD2"),
        v3.fill_null(False).alias("sig_V3_CQD3M"),
        ctrl.fill_null(False).alias("sig_CTRL"),
        score.fill_null(0.0).alias("sc_cqd"),
        (pl.concat_str([c("code"), c("trade_date")]).hash(202) % 1000003).cast(pl.Float64).alias("sc_rand"),
    )
    return df.select(
        "code", "trade_date", "ao", "ac", "lim_up_open", "lock_down", "sc_cqd", "sc_rand",
        *[x for x in df.columns if x.startswith("sig_")],
    )


def ex(a, e, i):
    return a["ac"][i] < a["ao"][e] * STOP or i - e >= HOLD - 1


def split(t, curve):
    res = {}
    ds, eq = curve
    ds = np.array(ds)
    for p, (a, b) in PHASES.items():
        tt = t.filter(c("entry").is_between(pl.lit(a), pl.lit(b)))
        m = (ds >= a) & (ds <= b)
        if tt.height == 0 or m.sum() == 0:
            res[p] = {"n": tt.height}
            continue
        r = tt["ret"].to_numpy()
        w = r[r > 0].sum(); l = -r[r < 0].sum()
        streak = mx = 0
        for x in tt.sort("entry")["ret"].to_list():
            streak = streak + 1 if x <= 0 else 0
            mx = max(mx, streak)
        rets_by_day = {}
        for row in tt.select("entry", "ret").iter_rows(named=True):
            rets_by_day.setdefault(row["entry"], 0.0)
            rets_by_day[row["entry"]] += row["ret"] / K
        days_sorted = sorted(rets_by_day)
        nav = [1.0]
        for d in days_sorted:
            nav.append(nav[-1] + rets_by_day[d])
        nav = np.array(nav)
        res[p] = {
            "n": len(r), "win": round(100 * (r > 0).mean(), 1), "avg": round(100 * r.mean(), 2),
            "pf": round(w / l, 2) if l > 0 else 99, "days": round(float(tt["days"].mean()), 2),
            "avg_win": round(100 * float(r[r > 0].mean()), 2) if (r > 0).any() else 0.0,
            "avg_loss": round(100 * float(r[r <= 0].mean()), 2) if (r <= 0).any() else 0.0,
            "max_loss_streak": mx,
            "tot": round(100 * (nav[-1] - 1), 1),
            "mdd": round(100 * (nav / np.maximum.accumulate(nav) - 1).min(), 1),
        }
    return res


def main(out_path):
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df = panel()
    print("panel", df.shape, flush=True)
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals/yr", df.filter(c("trade_date") >= START).group_by(yr).agg(
        *[c(x).sum() for x in df.columns if x.startswith("sig_")]).sort("y").to_dicts(), flush=True)
    res = {}
    specs = (
        ("V1_CQD3", "sc_cqd"),
        ("V2_CQD2", "sc_cqd"),
        ("V3_CQD3M", "sc_cqd"),
        ("CTRL", "sc_rand"),
    )
    for name, sc in specs:
        t, curve = run_real(df, f"sig_{name}", sc, ex, START, END, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        res[name] = split(t, curve)
        r = res[name]
        print(name, " ".join(
            f"{p.split()[0]}:{r[p].get('n', 0)}/{r[p].get('avg', 0):+.2f}/pf{r[p].get('pf', 0)}"
            for p in PHASES), flush=True)
        t.write_parquet(out.with_name(f"trades_{name}.parquet"))
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
