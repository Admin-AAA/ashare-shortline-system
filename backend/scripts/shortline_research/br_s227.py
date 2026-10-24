"""[短线研究 §227] 连跌后放量收阳突破前高（预注册 docs/strategy-research.md §227，结果出来前写定，不调参）。
行情 = midterm 长面板 + realistic 引擎；只用本地数据。
用法：cd backend/scripts/midterm && PYTHONPATH=../..:. ../../.venv/bin/python ../shortline_research/br_s227.py ../../output/research/s227/res.json"""
import sys, json
from pathlib import Path
import numpy as np
import polars as pl
import lt_data as L
from realistic import add_limits, run_real
from lt_research import PH

c = pl.col
MAX_NEW, K, HOLD, STOP = 3, 15, 3, 0.93
VR_MIN = 1.5
SCORE_CAP = 0.15
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
        (c("high") * kf).alias("ah"),
        c("close").shift(1).over("code").alias("pre_c"),
        c("volume").shift(1).rolling_mean(20).over("code").alias("volume_ma20"),
    )
    df = df.with_columns(
        pl.when(c("pre_c") > 0).then(c("close") < c("pre_c")).otherwise(False).alias("down"),
        pl.when(c("volume_ma20") > 0).then(c("volume") / c("volume_ma20")).otherwise(None).alias("vr"),
        (c("close") > c("open")).alias("yang"),
    )
    # past N downs (shift 1..N) and HH of those N days
    for n in (3, 4, 5):
        downs = pl.all_horizontal([c("down").shift(i).over("code").fill_null(False) for i in range(1, n + 1)])
        hh = pl.max_horizontal([c("ah").shift(i).over("code") for i in range(1, n + 1)])
        df = df.with_columns(downs.alias(f"dn{n}"), hh.alias(f"hh{n}"))
        df = df.with_columns(
            pl.when(c(f"hh{n}") > 0).then(c("ac") / c(f"hh{n}") - 1.0).otherwise(None).alias(f"bo{n}")
        )
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
        & c("vr").is_not_null()
        & c("hh3").is_not_null()
    )
    base = uni & not_ow_up & not_lock_dn
    core3 = c("dn3") & c("yang") & (c("vr") >= VR_MIN) & (c("ac") > c("hh3"))
    v1 = base & core3
    v2 = base & c("dn4") & c("yang") & (c("vr") >= VR_MIN) & (c("ac") > c("hh4"))
    v3 = base & c("dn5") & c("yang") & (c("vr") >= VR_MIN) & (c("ac") > c("hh5"))
    ctrl = base & ~core3.fill_null(False)
    sc3 = (c("bo3").fill_null(-1.0) * c("vr").fill_null(0.0)).clip(-1e9, SCORE_CAP)
    sc4 = (c("bo4").fill_null(-1.0) * c("vr").fill_null(0.0)).clip(-1e9, SCORE_CAP)
    sc5 = (c("bo5").fill_null(-1.0) * c("vr").fill_null(0.0)).clip(-1e9, SCORE_CAP)
    df = df.with_columns(
        v1.fill_null(False).alias("sig_V1_BR3"),
        v2.fill_null(False).alias("sig_V2_BR4"),
        v3.fill_null(False).alias("sig_V3_BR5"),
        ctrl.fill_null(False).alias("sig_CTRL"),
        sc3.alias("sc_br3"),
        sc4.alias("sc_br4"),
        sc5.alias("sc_br5"),
        (pl.concat_str([c("code"), c("trade_date")]).hash(227) % 1000003).cast(pl.Float64).alias("sc_rand"),
    )
    return df.select(
        "code", "trade_date", "ao", "ac", "lim_up_open", "lock_down",
        "sc_br3", "sc_br4", "sc_br5", "sc_rand",
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
        ("V1_BR3", "sc_br3"),
        ("V2_BR4", "sc_br4"),
        ("V3_BR5", "sc_br5"),
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
