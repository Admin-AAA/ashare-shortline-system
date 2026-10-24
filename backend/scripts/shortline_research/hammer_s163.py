"""[短线研究 §163] 长下影线反转 Hammer/pin-bar（预注册 docs/strategy-research.md §163，结果出来前写定，不调参）。
行情 = midterm 长面板 + realistic 引擎；只用本地数据。
用法：cd backend/scripts/midterm && PYTHONPATH=../..:. ../../.venv/bin/python ../shortline_research/hammer_s163.py ../../output/research/s163/res.json"""
import sys, json
from pathlib import Path
import numpy as np
import polars as pl
import lt_data as L
from realistic import add_limits, run_real
from lt_research import PH

c = pl.col
MAX_NEW, K, HOLD, STOP = 3, 15, 3, 0.93
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
        c("volume").shift(1).rolling_mean(20).over("code").alias("volume_ma20"),
    )
    rng = c("high") - c("low")
    df = df.with_columns(
        pl.when(c("pre_c") > 0).then(rng / c("pre_c")).otherwise(None).alias("range_pct"),
        pl.when(rng > 0).then((pl.min_horizontal(c("open"), c("close")) - c("low")) / rng).otherwise(None).alias("wick"),
        pl.when(rng > 0).then((c("close") - c("low")) / rng).otherwise(None).alias("clv"),
        (c("close") / c("pre_c") - 1).alias("day_ret"),
        pl.when(c("volume_ma20") > 0).then(c("volume") / c("volume_ma20")).otherwise(None).alias("vr"),
    )
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.sort("trade_date").join_asof(st, on="trade_date", by="code", strategy="backward").with_columns(c("st").fill_null(False))
    df = add_limits(df.sort("code", "trade_date"))
    uni = (
        (~c("st"))
        & (c("age") >= 120)
        & (c("amount") >= 1e8)
        & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
        & c("pre_c").is_not_null()
        & (c("pre_c") > 0)
        & (c("open") > 0)
        & c("range_pct").is_not_null()
        & c("wick").is_not_null()
    )
    v1 = uni & (c("range_pct") >= 0.04) & (c("wick") >= 0.6) & (c("clv") >= 0.5) & (c("day_ret") <= 0.02)
    v2 = v1 & (c("day_ret") <= 0.0)
    v3 = v1 & (c("vr") >= 1.5)
    ctrl = uni & (c("range_pct") >= 0.04) & (c("wick") <= 0.25) & (c("clv") <= 0.4)
    df = df.with_columns(
        v1.fill_null(False).alias("sig_V1_HAM"),
        v2.fill_null(False).alias("sig_V2_HAM_DN"),
        v3.fill_null(False).alias("sig_V3_HAM_VOL"),
        ctrl.fill_null(False).alias("sig_CTRL"),
        c("wick").fill_null(0).alias("sc_wick"),
        (pl.concat_str([c("code"), c("trade_date")]).hash(163) % 1000003).cast(pl.Float64).alias("sc_rand"),
    )
    return df.select(
        "code", "trade_date", "ao", "ac", "lim_up_open", "lock_down", "sc_wick", "sc_rand",
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
        i0 = int(np.argmax(m))
        base = eq[i0 - 1] if i0 > 0 else 1.0
        seg = np.concatenate([[1.0], 1.0 + (eq[m] - base)])
        r = tt["ret"].to_numpy()
        w = r[r > 0].sum(); l = -r[r < 0].sum()
        streak = mx = 0
        for x in tt.sort("entry")["ret"].to_list():
            streak = streak + 1 if x <= 0 else 0
            mx = max(mx, streak)
        res[p] = {
            "n": len(r), "win": round(100 * (r > 0).mean(), 1), "avg": round(100 * r.mean(), 2),
            "pf": round(w / l, 2) if l > 0 else 99, "days": round(float(tt["days"].mean()), 2),
            "avg_win": round(100 * float(r[r > 0].mean()), 2) if (r > 0).any() else 0.0,
            "avg_loss": round(100 * float(r[r <= 0].mean()), 2) if (r <= 0).any() else 0.0,
            "max_loss_streak": mx,
            "tot": round(100 * (seg[-1] - 1), 1),
            "mdd": round(100 * (seg / np.maximum.accumulate(seg) - 1).min(), 1),
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
    for name in ("V1_HAM", "V2_HAM_DN", "V3_HAM_VOL", "CTRL"):
        sc = "sc_rand" if name == "CTRL" else "sc_wick"
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
