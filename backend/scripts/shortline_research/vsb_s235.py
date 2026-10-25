"""[短线研究 §235] 缩量整理后放量收盘突破 Vol-Shrink Breakout（预注册 docs/strategy-research.md §235，结果出来前写定，不调参）。
行情 = midterm 长面板 + realistic 引擎；只用本地数据。
用法：cd backend/scripts/midterm && PYTHONPATH=../..:. ../../.venv/bin/python ../shortline_research/vsb_s235.py ../../output/research/s235/res.json"""
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
VR_SHRINK, VR_BREAK, VR_STRONG = 0.7, 1.2, 1.5


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
        c("high").shift(1).over("code").alias("h1"),
        c("high").shift(2).over("code").alias("h2"),
        c("high").shift(3).over("code").alias("h3"),
        c("high").shift(4).over("code").alias("h4"),
        c("high").shift(5).over("code").alias("h5"),
    )
    df = df.with_columns(
        pl.when(c("volume_ma20") > 0).then(c("volume") / c("volume_ma20")).otherwise(None).alias("vr"),
    )
    df = df.with_columns(
        c("vr").shift(1).over("code").alias("vr1"),
        c("vr").shift(2).over("code").alias("vr2"),
        c("vr").shift(3).over("code").alias("vr3"),
        c("vr").shift(4).over("code").alias("vr4"),
        c("vr").shift(5).over("code").alias("vr5"),
    )
    df = df.with_columns(
        pl.max_horizontal("h1", "h2", "h3").alias("hh3"),
        pl.max_horizontal("h1", "h2", "h3", "h4", "h5").alias("hh5"),
    )
    shrink3 = (
        (c("vr1") < VR_SHRINK) & (c("vr2") < VR_SHRINK) & (c("vr3") < VR_SHRINK)
        & c("vr1").is_not_null() & c("vr2").is_not_null() & c("vr3").is_not_null()
    )
    shrink5 = shrink3 & (c("vr4") < VR_SHRINK) & (c("vr5") < VR_SHRINK) & c("vr4").is_not_null() & c("vr5").is_not_null()
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
        & c("volume_ma20").is_not_null()
        & (c("volume_ma20") > 0)
        & c("hh3").is_not_null()
        & (c("hh3") > 0)
    )
    br3 = shrink3 & (c("close") > c("hh3")) & (c("vr") >= VR_BREAK) & not_ow_up
    br5 = shrink5 & (c("close") > c("hh5")) & (c("vr") >= VR_BREAK) & not_ow_up & c("hh5").is_not_null() & (c("hh5") > 0)
    v1 = uni & br3
    v2 = v1 & (c("vr") >= VR_STRONG)
    v3 = uni & br5
    any_br = (br3 | br5).fill_null(False)
    ctrl = uni & ~any_br
    prem3 = (c("close") - c("hh3")) / c("hh3").clip(lower_bound=1e-6)
    prem5 = (c("close") - c("hh5")) / c("hh5").clip(lower_bound=1e-6)
    df = df.with_columns(
        v1.fill_null(False).alias("sig_V1_VSB3"),
        v2.fill_null(False).alias("sig_V2_VSB3_VR15"),
        v3.fill_null(False).alias("sig_V3_VSB5"),
        ctrl.fill_null(False).alias("sig_CTRL"),
        (-prem3.fill_null(1.0)).alias("sc_vsb3"),
        (-prem5.fill_null(1.0)).alias("sc_vsb5"),
        (pl.concat_str([c("code"), c("trade_date")]).hash(235) % 1000003).cast(pl.Float64).alias("sc_rand"),
    )
    return df.select(
        "code", "trade_date", "ao", "ac", "lim_up_open", "lock_down", "sc_vsb3", "sc_vsb5", "sc_rand",
        *[x for x in df.columns if x.startswith("sig_")],
    )


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


def ex(a, e, i):
    return a["ac"][i] < a["ao"][e] * STOP or i - e >= HOLD - 1


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
        "V1_VSB3": "sc_vsb3",
        "V2_VSB3_VR15": "sc_vsb3",
        "V3_VSB5": "sc_vsb5",
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
