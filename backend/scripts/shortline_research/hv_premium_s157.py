"""[短线研究 §157] 放量不大涨后的高成交量溢价（预注册 docs/strategy-research.md §157，结果出来前写定，不调参）。
行情 = midterm 长面板 + realistic 引擎；只用本地数据。
用法：cd backend/scripts/midterm && PYTHONPATH=../..:. ../../.venv/bin/python ../shortline_research/hv_premium_s157.py out.json"""
import sys, json
from pathlib import Path
import numpy as np
import polars as pl
import lt_data as L
from realistic import add_limits, run_real
from lt_research import PH

c = pl.col
MAX_NEW, K, HOLD, STOP = 3, 15, 5, 0.93
PHASES = dict(PH)
PHASES["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
START, END = "2019-01-01", "2026-10-16"


def panel():
    df = L.prices(START, END)
    df = df.with_columns(((1 + c("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code")).alias("ac"))
    kf = c("ac") / c("close")
    df = df.with_columns((c("open") * kf).alias("ao"), pl.int_range(pl.len()).over("code").alias("age"),
                         (c("volume") / c("volume").shift(1).rolling_mean(20).over("code")).alias("vr"),
                         (c("ac") / c("ac").shift(60).over("code") - 1).alias("r60"),
                         pl.when(c("high") > c("low")).then((c("close") - c("low")) / (c("high") - c("low"))).otherwise(None).alias("clv"))
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.sort("trade_date").join_asof(st, on="trade_date", by="code", strategy="backward").with_columns(c("st").fill_null(False))
    df = add_limits(df.sort("code", "trade_date"))
    uni = (~c("st")) & (c("age") >= 120) & (c("amount") >= 1e8) & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
    flat = c("pct_chg").abs() <= 3
    v1 = uni & flat & (c("vr") >= 3)
    df = df.with_columns(
        v1.fill_null(False).alias("sig_V1_HV"),
        (v1 & (c("clv") >= 0.5)).fill_null(False).alias("sig_V2_HV_CLOSE"),
        (v1 & c("r60").is_between(-0.20, 0.20)).fill_null(False).alias("sig_V3_HV_BASE"),
        (uni & flat & c("vr").is_between(0.8, 1.2)).fill_null(False).alias("sig_CTRL"),
        c("vr").fill_null(0).alias("sc_vr"),
        (pl.concat_str([c("code"), c("trade_date")]).hash(157) % 1000003).cast(pl.Float64).alias("sc_rand"))
    return df.select("code", "trade_date", "ao", "ac", "lim_up_open", "lock_down", "sc_vr", "sc_rand",
                     *[x for x in df.columns if x.startswith("sig_")])


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
        base = eq[i0 - 1] if i0 > 0 else 1.0  # 固定名义仓位：阶段内按加法重置到 1（避免长期累计亏损后比值失真）
        seg = np.concatenate([[1.0], 1.0 + (eq[m] - base)])
        r = tt["ret"].to_numpy(); w = r[r > 0].sum(); l = -r[r < 0].sum()
        streak = mx = 0
        for x in tt.sort("entry")["ret"].to_list():
            streak = streak + 1 if x <= 0 else 0; mx = max(mx, streak)
        res[p] = {"n": len(r), "win": round(100 * (r > 0).mean(), 1), "avg": round(100 * r.mean(), 2),
                  "pf": round(w / l, 2) if l > 0 else 99, "days": round(float(tt["days"].mean()), 2),
                  "avg_win": round(100 * float(r[r > 0].mean()), 2) if (r > 0).any() else 0.0,
                  "avg_loss": round(100 * float(r[r <= 0].mean()), 2) if (r <= 0).any() else 0.0,
                  "max_loss_streak": mx,
                  "tot": round(100 * (seg[-1] - 1), 1), "mdd": round(100 * (seg / np.maximum.accumulate(seg) - 1).min(), 1)}
    return res


def main(out_path):
    df = panel()
    print("panel", df.shape, flush=True)
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals/yr", df.filter(c("trade_date") >= START).group_by(yr).agg(
        *[c(x).sum() for x in df.columns if x.startswith("sig_")]).sort("y").to_dicts(), flush=True)
    res = {}
    for name in ("V1_HV", "V2_HV_CLOSE", "V3_HV_BASE", "CTRL"):
        sc = "sc_rand" if name == "CTRL" else "sc_vr"
        t, curve = run_real(df, f"sig_{name}", sc, ex, START, END, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        res[name] = split(t, curve)
        r = res[name]
        print(name, " ".join(f"{p.split()[0]}:{r[p].get('n', 0)}/{r[p].get('avg', 0):+.2f}/pf{r[p].get('pf', 0)}" for p in PHASES), flush=True)
        t.write_parquet(Path(out_path).with_name(f"trades_{name}.parquet"))
    Path(out_path).write_text(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
