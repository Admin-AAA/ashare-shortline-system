"""[短线研究 §239] 连跌后重新站上 MA5 Consecutive-Down MA5 Reclaim（预注册 docs/strategy-research.md §239，结果出来前写定，不调参）。
行情 = midterm 长面板 + realistic 引擎；只用本地数据。
用法：cd backend/scripts/midterm && PYTHONPATH=../..:. ../../.venv/bin/python ../shortline_research/cdr_s239.py ../../output/research/s239/res.json"""
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
VR_MIN = 1.2


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
        c("close").rolling_mean(5).over("code").alias("ma5"),
    )
    df = df.with_columns(
        c("ma5").shift(1).over("code").alias("pre_ma5"),
        pl.when(c("volume_ma20") > 0).then(c("volume") / c("volume_ma20")).otherwise(None).alias("vr"),
        (c("close") < c("pre_c")).alias("down"),
    )
    df = df.with_columns(
        *[c("down").shift(i).over("code").alias(f"down{i}") for i in range(1, 6)],
        ((c("close") - c("ma5")) / c("ma5").clip(lower_bound=1e-6)).alias("prem"),
    )
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.sort("trade_date").join_asof(st, on="trade_date", by="code", strategy="backward").with_columns(c("st").fill_null(False))
    df = add_limits(df.sort("code", "trade_date"))
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    up_lim = pl.coalesce(c("up_limit"), c("pre_c") * (1 + band))
    not_ow_up = ~((c("low") >= up_lim - 0.005).fill_null(False))
    uni = (
        (~c("st"))
        & (c("age") >= 120)
        & (c("amount") >= 1e8)
        & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
        & c("pre_c").is_not_null()
        & (c("pre_c") > 0)
        & (c("open") > 0)
        & c("ma5").is_not_null()
        & c("pre_ma5").is_not_null()
        & (c("ma5") > 0)
        & (c("pre_ma5") > 0)
        & c("volume_ma20").is_not_null()
        & (c("volume_ma20") > 0)
    )
    down3 = (
        c("down1").fill_null(False) & c("down2").fill_null(False) & c("down3").fill_null(False)
        & c("down1").is_not_null() & c("down2").is_not_null() & c("down3").is_not_null()
    )
    down5 = down3 & c("down4").fill_null(False) & c("down5").fill_null(False) & c("down4").is_not_null() & c("down5").is_not_null()
    reclaim = (c("pre_c") <= c("pre_ma5")) & (c("close") > c("ma5"))
    v1 = uni & down3 & reclaim & not_ow_up
    v2 = v1 & (c("vr") >= VR_MIN)
    v3 = uni & down5 & reclaim & not_ow_up
    any_cdr = ((down3 & reclaim) | (down5 & reclaim)).fill_null(False)
    ctrl = uni & ~any_cdr
    df = df.with_columns(
        v1.fill_null(False).alias("sig_V1_CDR3"),
        v2.fill_null(False).alias("sig_V2_CDR3_VR12"),
        v3.fill_null(False).alias("sig_V3_CDR5"),
        ctrl.fill_null(False).alias("sig_CTRL"),
        (-c("prem").abs().fill_null(1.0)).alias("sc_cdr"),
        (pl.concat_str([c("code"), c("trade_date")]).hash(239) % 1000003).cast(pl.Float64).alias("sc_rand"),
    )
    return df.select(
        "code", "trade_date", "ao", "ac", "lim_up_open", "lock_down", "sc_cdr", "sc_rand",
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
    score_map = {
        "V1_CDR3": "sc_cdr",
        "V2_CDR3_VR12": "sc_cdr",
        "V3_CDR5": "sc_cdr",
        "CTRL": "sc_rand",
    }
    for name, sc in score_map.items():
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
