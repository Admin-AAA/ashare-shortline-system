"""[短线研究 §199] 相对行业等权相对强弱 RS vs Industry（预注册 docs/strategy-research.md §199，结果出来前写定，不调参）。
行情 = midterm 长面板 + industry_cache + realistic 引擎；只用本地数据。
用法：cd backend/scripts/midterm && PYTHONPATH=../..:. ../../.venv/bin/python ../shortline_research/rs_s199.py ../../output/research/s199/res.json"""
import sys, json
from pathlib import Path
import numpy as np
import polars as pl
import lt_data as L
from realistic import add_limits, run_real
from lt_research import PH

c = pl.col
MAX_NEW, K, HOLD, STOP = 3, 15, 3, 0.93
RS5_MIN, RS10_MIN, TOP_N, VOL_MULT = 0.03, 0.05, 30, 1.3
IND_MIN = 5
PHASES = dict(PH)
PHASES["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
START, END = "2019-01-01", "2026-10-16"
ROOT = Path(__file__).resolve().parents[2]


def _load_industry():
    import json as _j
    raw = _j.load(open(ROOT / "data" / "industry_cache.json"))
    # keys are bare codes like "000001"; panel codes may be "000001.SZ"
    rows = []
    for k, v in raw.items():
        code = str(k).split(".")[0].zfill(6)
        if v:
            rows.append({"code6": code, "industry": str(v)})
    return pl.DataFrame(rows).unique("code6")


def panel():
    df = L.prices(START, END)
    ind = _load_industry()
    df = df.with_columns(
        c("code").cast(pl.Utf8).str.split(".").list.first().str.zfill(6).alias("code6"),
        ((1 + c("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code")).alias("ac"),
        pl.int_range(pl.len()).over("code").alias("age"),
    )
    df = df.join(ind, on="code6", how="left")
    kf = c("ac") / c("close")
    df = df.with_columns(
        (c("open") * kf).alias("ao"),
        c("close").shift(1).over("code").alias("pre_c"),
        c("volume").shift(1).rolling_mean(20).over("code").alias("volume_ma20"),
        c("ac").shift(5).over("code").alias("ac5"),
        c("ac").shift(10).over("code").alias("ac10"),
    )
    df = df.with_columns(
        pl.when(c("volume_ma20") > 0).then(c("volume") / c("volume_ma20")).otherwise(None).alias("vr"),
        pl.when(c("ac5") > 0).then(c("ac") / c("ac5") - 1.0).otherwise(None).alias("ret5"),
        pl.when(c("ac10") > 0).then(c("ac") / c("ac10") - 1.0).otherwise(None).alias("ret10"),
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
        & c("industry").is_not_null()
        & c("ret5").is_not_null()
        & c("ret10").is_not_null()
    )
    df = df.with_columns(uni.fill_null(False).alias("uni"))
    # industry equal-weight among uni members
    ind_agg = (
        df.filter(c("uni"))
        .group_by("trade_date", "industry")
        .agg(
            c("ret5").mean().alias("ind_ret5"),
            c("ret10").mean().alias("ind_ret10"),
            pl.len().alias("ind_n"),
        )
        .filter(c("ind_n") >= IND_MIN)
    )
    df = df.join(ind_agg, on=["trade_date", "industry"], how="left")
    df = df.with_columns(
        (c("ret5") - c("ind_ret5")).alias("rs5"),
        (c("ret10") - c("ind_ret10")).alias("rs10"),
    )
    # cross-sectional rank among uni with industry ok
    base = c("uni") & c("ind_ret5").is_not_null() & not_ow_up
    df = df.with_columns(
        pl.when(base).then(c("rs5")).otherwise(None).alias("rs5_rank_key"),
        pl.when(base).then(c("rs10")).otherwise(None).alias("rs10_rank_key"),
    )
    df = df.with_columns(
        c("rs5_rank_key").rank(method="ordinal", descending=True).over("trade_date").alias("rk5"),
        c("rs10_rank_key").rank(method="ordinal", descending=True).over("trade_date").alias("rk10"),
    )
    v1 = base & (c("rs5") >= RS5_MIN) & (c("rk5") <= TOP_N)
    v2 = base & (c("rs10") >= RS10_MIN) & (c("rk10") <= TOP_N)
    v3 = v1 & (c("vr") >= VOL_MULT)
    ctrl = base & ~((c("rs5") >= RS5_MIN) & (c("rk5") <= TOP_N)).fill_null(False)
    df = df.with_columns(
        v1.fill_null(False).alias("sig_V1_RS5"),
        v2.fill_null(False).alias("sig_V2_RS10"),
        v3.fill_null(False).alias("sig_V3_RS5V"),
        ctrl.fill_null(False).alias("sig_CTRL"),
        c("rs5").fill_null(-1.0).alias("sc_rs5"),
        c("rs10").fill_null(-1.0).alias("sc_rs10"),
        (pl.concat_str([c("code"), c("trade_date")]).hash(199) % 1000003).cast(pl.Float64).alias("sc_rand"),
    )
    return df.select(
        "code", "trade_date", "ao", "ac", "lim_up_open", "lock_down", "sc_rs5", "sc_rs10", "sc_rand",
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
        ("V1_RS5", "sc_rs5"),
        ("V2_RS10", "sc_rs10"),
        ("V3_RS5V", "sc_rs5"),
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
