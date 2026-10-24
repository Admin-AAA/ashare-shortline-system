"""[短线研究 §165] 连续下跌后的反转 Consecutive Down Days（预注册 docs/strategy-research.md §165，结果出来前写定，不调参）。
行情 = midterm 长面板 + realistic 引擎；只用本地数据。
用法：cd backend/scripts/midterm && PYTHONPATH=../..:. ../../.venv/bin/python ../shortline_research/cdd_s165.py ../../output/research/s165/res.json"""
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
        c("close").shift(3).over("code").alias("c3"),
    )
    rng = c("high") - c("low")
    df = df.with_columns(
        (c("close") < c("pre_c")).alias("down"),
        (c("close") / c("pre_c") - 1).alias("day_ret"),
        pl.when(rng > 0).then((c("close") - c("low")) / rng).otherwise(None).alias("clv"),
        pl.when(c("volume_ma20") > 0).then(c("volume") / c("volume_ma20")).otherwise(None).alias("vr"),
        pl.when(c("c3") > 0).then(c("close") / c("c3") - 1).otherwise(None).alias("cum3"),
    )
    df = df.with_columns(
        c("down").shift(1).over("code").alias("down1"),
        c("down").shift(2).over("code").alias("down2"),
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
        & c("down1").is_not_null()
        & c("down2").is_not_null()
        & c("cum3").is_not_null()
    )
    cdd3 = c("down") & c("down1") & c("down2")
    not_ld = ~c("lock_down").fill_null(False)
    v1 = uni & cdd3 & (c("day_ret") > -0.07) & not_ld
    v2 = v1 & (c("clv") >= 0.5)
    v3 = v1 & (c("vr") <= 0.8)
    ctrl = uni & ~(cdd3.fill_null(False))
    df = df.with_columns(
        v1.fill_null(False).alias("sig_V1_CDD3"),
        v2.fill_null(False).alias("sig_V2_CDD3_CLV"),
        v3.fill_null(False).alias("sig_V3_CDD3_DRY"),
        ctrl.fill_null(False).alias("sig_CTRL"),
        (-c("cum3").fill_null(0)).alias("sc_drop"),  # 跌越多分数越高
        (pl.concat_str([c("code"), c("trade_date")]).hash(165) % 1000003).cast(pl.Float64).alias("sc_rand"),
    )
    return df.select(
        "code", "trade_date", "ao", "ac", "lim_up_open", "lock_down", "sc_drop", "sc_rand",
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
        # 固定名义仓位（1/K）阶段内加法重置，避免长期亏损后比值失真（同 §157 修正）
        r = tt["ret"].to_numpy()
        w = r[r > 0].sum(); l = -r[r < 0].sum()
        streak = mx = 0
        for x in tt.sort("entry")["ret"].to_list():
            streak = streak + 1 if x <= 0 else 0
            mx = max(mx, streak)
        # 用曲线段计算回撤（加法重置：从阶段起点按固定 1/K 名义累加）
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
    for name in ("V1_CDD3", "V2_CDD3_CLV", "V3_CDD3_DRY", "CTRL"):
        sc = "sc_rand" if name == "CTRL" else "sc_drop"
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
