"""[短线研究 §1004–§1006] 三个未测过的新方向（预注册 docs/strategy-research.md §1004–§1006，结果出来前写定，不调参）。

§1004 SEC_DIP_h5「行业热点 × 个股深跌」：
  行业 5 日等权收益排名前 3 且个股 r20 ≤ −10%。
  去重：L-179 板块补涨（无深跌过滤，已删）；§199 RS vs industry（选领涨不是滞后）；
  §993/§997/§1000（公司事件×深跌，不是行业轮动×深跌）。

§1005 VCE_h5「连续缩量后首次放量阳线」：
  过去 5 日中至少 4 日成交量逐日递减，今日量 ≥ 2× 前 5 日均量，收阳，近 5 日涨幅 ≤ 5%。
  去重：§157 放量溢价（要求价格平稳，无缩量趋势前提）；§202 安静日（要求极窄振幅，无量趋势）；
  §165 连续下跌（无量要求）。

§1006 LULOW_h3「涨停次日平开收阳后买入」：
  T−1 封涨停，T 日开盘 ≤ T−1 收盘×1.02（未大幅高开），T 收阳且收盘 ≥ T−1 收盘×0.95。
  去重：§156 题材龙头（T 日涨停、T+1 开盘买，不等确认日）；§274 炸板阳线（当日触板未封）；
  §325/§326/§327 追龙（分钟线/预测连板）。

行情 = midterm 长面板 + realistic 引擎；只用本地数据 + industry_cache.json。
用法：cd backend/scripts/midterm && PYTHONPATH=../..:.:../shortline_research ../../.venv/bin/python ../shortline_research/secdip_s1004.py out.json
"""
import sys, json, os
from pathlib import Path
import numpy as np
import polars as pl
import lt_data as L
from realistic import add_limits, run_real
from lt_research import PH

c = pl.col
MAX_NEW, K = 3, 15
STOP = 0.93
START, END = "2019-01-01", "2026-10-16"
HOLD_5, HOLD_3 = 5, 3

PHASES = dict(PH)
PHASES["EXT 2024-01~2025-09"] = ("2024-01-01", "2025-09-30")
PHASES["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")

IND_CACHE = Path(__file__).resolve().parents[2] / "data/industry_cache.json"


def panel():
    df = L.prices(START, END)
    df = df.with_columns(((1 + c("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code")).alias("ac"))
    kf = c("ac") / c("close")
    df = df.with_columns(
        (c("open") * kf).alias("ao"),
        pl.int_range(pl.len()).over("code").alias("age"),
        (c("volume") / c("volume").shift(1).rolling_mean(20).over("code")).alias("vr20"),
        (c("ac") / c("ac").shift(20).over("code") - 1).alias("r20"),
        (c("ac") / c("ac").shift(5).over("code") - 1).alias("r5"),
        (c("ac") / c("ac").shift(1).over("code") - 1).alias("r1"),
        c("volume").shift(1).over("code").alias("vol_prev1"),
        c("volume").shift(2).over("code").alias("vol_prev2"),
        c("volume").shift(3).over("code").alias("vol_prev3"),
        c("volume").shift(4).over("code").alias("vol_prev4"),
        c("volume").shift(5).over("code").alias("vol_prev5"),
        c("volume").rolling_mean(5).over("code").shift(1).alias("vol_ma5_prev"),
    )
    # ST
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.sort("trade_date").join_asof(st, on="trade_date", by="code", strategy="backward").with_columns(c("st").fill_null(False))
    df = add_limits(df.sort("code", "trade_date"))

    # limit-up flag (close at limit-up)
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    pc = c("close").shift(1).over("code")
    ul = pl.coalesce(c("up_limit"), pc * (1 + band))
    df = df.with_columns(
        (c("close") >= ul - 0.005).fill_null(False).alias("is_lu"),
        (c("close").shift(1).over("code") >= ul.shift(1).over("code") - 0.005).fill_null(False).alias("prev_lu"),
    )

    # industry
    ind = json.loads(IND_CACHE.read_text())
    df = df.with_columns(c("code").replace_strict(ind, default=None).alias("industry"))

    # universe
    uni = (~c("st")) & (c("age") >= 120) & (c("amount") >= 1e8) & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
    df = df.with_columns(uni.fill_null(False).alias("uni"))

    # ── §1004: industry 5-day equal-weight return, top 3 industries ──
    # compute per-stock r5 already done; industry avg r5 per day
    ind_r5 = (df.filter(c("uni") & c("industry").is_not_null() & c("r5").is_not_null())
              .group_by(["trade_date", "industry"])
              .agg(c("r5").mean().alias("ind_r5"), pl.len().alias("ind_n"))
              .filter(c("ind_n") >= 5))
    # rank industries per day
    ind_r5 = ind_r5.with_columns(c("ind_r5").rank(descending=True).over("trade_date").alias("ind_rank"))
    df = df.join(ind_r5.select("trade_date", "industry", "ind_r5", "ind_rank"), on=["trade_date", "industry"], how="left")

    # stock RS vs industry (5-day)
    df = df.with_columns((c("r5") - c("ind_r5")).alias("rs5_ind"))

    # ── §1005: volume contraction count ──
    # declining: vol_prev1 < vol_prev2, vol_prev2 < vol_prev3, etc.
    df = df.with_columns(
        ((c("vol_prev1") < c("vol_prev2")).cast(pl.Int8)
         + (c("vol_prev2") < c("vol_prev3")).cast(pl.Int8)
         + (c("vol_prev3") < c("vol_prev4")).cast(pl.Int8)
         + (c("vol_prev4") < c("vol_prev5")).cast(pl.Int8)
         ).alias("vol_decline_cnt")
    )

    # ── Signals ──
    # §1004 SEC_DIP: industry top 3 by r5 AND stock r20 <= -10%
    sig_1004 = (c("uni") & (c("ind_rank") <= 3) & (c("r20") <= -0.10)
                & (c("rs5_ind") <= -0.03) & ~c("is_lu"))
    # §1004 CTRL: industry top 3 but stock r20 > -5% (not deeply declined)
    ctrl_1004 = (c("uni") & (c("ind_rank") <= 3) & (c("r20") > -0.05) & ~c("is_lu"))

    # §1005 VCE: 4/5 days volume declining + today vol >= 2x ma5_prev + yang + r5 <= 5%
    sig_1005 = (c("uni") & (c("vol_decline_cnt") >= 4)
                & (c("volume") >= 2.0 * c("vol_ma5_prev"))
                & (c("close") > c("open"))
                & (c("r5").fill_null(9) <= 0.05)
                & ~c("is_lu"))
    # §1005 CTRL: yang + vol >= 2x ma5 but WITHOUT prior contraction (vol_decline_cnt <= 1)
    ctrl_1005 = (c("uni") & (c("vol_decline_cnt") <= 1)
                 & (c("volume") >= 2.0 * c("vol_ma5_prev"))
                 & (c("close") > c("open"))
                 & (c("r5").fill_null(9) <= 0.05)
                 & ~c("is_lu"))

    # §1006 LULOW: prev day limit-up, today open <= prev_close*1.02, today yang, today close >= prev_close*0.95
    prev_c = c("close").shift(1).over("code")
    sig_1006 = (c("uni") & c("prev_lu")
                & (c("open") <= prev_c * 1.02)
                & (c("close") > c("open"))
                & (c("close") >= prev_c * 0.95)
                & ~c("is_lu"))  # today not limit-up (we want the confirmation day, not another board)
    # §1006 CTRL: prev day strong (+5%~+9%) but NOT limit-up, same open/yang conditions
    ctrl_1006 = (c("uni") & ~c("prev_lu")
                 & (c("r1").fill_null(0) >= 0.05) & (c("r1").fill_null(0) <= 0.09)
                 & (c("open") <= prev_c * 1.02)
                 & (c("close") > c("open"))
                 & (c("close") >= prev_c * 0.95)
                 & ~c("is_lu"))

    # scores
    df = df.with_columns(
        sig_1004.fill_null(False).alias("sig_SEC_DIP"),
        ctrl_1004.fill_null(False).alias("sig_CTRL_1004"),
        sig_1005.fill_null(False).alias("sig_VCE"),
        ctrl_1005.fill_null(False).alias("sig_CTRL_1005"),
        sig_1006.fill_null(False).alias("sig_LULOW"),
        ctrl_1006.fill_null(False).alias("sig_CTRL_1006"),
        # scores: §1004 by r20 ascending (deepest decline first)
        (-c("r20").fill_null(-9)).alias("sc_dip"),
        # §1005 by volume expansion ratio descending
        (c("volume") / c("vol_ma5_prev").fill_null(1)).alias("sc_vol"),
        # §1006 by closeness to prev limit-up price (higher = better)
        (c("close") / prev_c).alias("sc_lu"),
        # random for CTRL
        (pl.concat_str([c("code"), c("trade_date")]).hash(1004) % 1000003).cast(pl.Float64).alias("sc_rand"),
    )
    keep = ["code", "trade_date", "ao", "ac", "lim_up_open", "lock_down",
            "sig_SEC_DIP", "sig_CTRL_1004", "sig_VCE", "sig_CTRL_1005", "sig_LULOW", "sig_CTRL_1006",
            "sc_dip", "sc_vol", "sc_lu", "sc_rand"]
    return df.select(keep)


def ex5(a, e, i):
    return a["ac"][i] < a["ao"][e] * STOP or i - e >= HOLD_5 - 1

def ex3(a, e, i):
    return a["ac"][i] < a["ao"][e] * STOP or i - e >= HOLD_3 - 1


def split(t, curve):
    res = {}
    if t is None or t.height == 0:
        for p in PHASES:
            res[p] = {"n": 0}
        return res
    ds, eq = curve
    ds_arr = np.array(ds)
    for p, (a, b) in PHASES.items():
        tt = t.filter(c("entry").is_between(pl.lit(a), pl.lit(b)))
        m = (ds_arr >= a) & (ds_arr <= b)
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
            streak = streak + 1 if x <= 0 else 0; mx = max(mx, streak)
        res[p] = {"n": len(r), "win": round(100 * (r > 0).mean(), 1), "avg": round(100 * r.mean(), 2),
                  "pf": round(w / l, 2) if l > 0 else 99, "days": round(float(tt["days"].mean()), 2),
                  "max_loss_streak": mx,
                  "tot": round(100 * (seg[-1] - 1), 1), "mdd": round(100 * (seg / np.maximum.accumulate(seg) - 1).min(), 1)}
    return res


def main(out_path):
    df = panel()
    print("panel", df.shape, flush=True)
    # signal counts per year
    yr = c("trade_date").str.slice(0, 4).alias("y")
    for s in ("sig_SEC_DIP", "sig_VCE", "sig_LULOW"):
        cnt = df.filter(c(s)).group_by(yr).len().sort("y")
        print(f"{s}/yr:", cnt.to_dicts(), flush=True)

    od = Path(out_path).parent
    od.mkdir(parents=True, exist_ok=True)
    res = {}
    configs = [
        ("SEC_DIP_h5", "sig_SEC_DIP", "sc_dip", ex5, HOLD_5),
        ("CTRL_1004", "sig_CTRL_1004", "sc_rand", ex5, HOLD_5),
        ("VCE_h5", "sig_VCE", "sc_vol", ex5, HOLD_5),
        ("CTRL_1005", "sig_CTRL_1005", "sc_rand", ex5, HOLD_5),
        ("LULOW_h3", "sig_LULOW", "sc_lu", ex3, HOLD_3),
        ("CTRL_1006", "sig_CTRL_1006", "sc_rand", ex3, HOLD_3),
    ]
    for name, sig, sc, exit_fn, hold in configs:
        t, curve = run_real(df, sig, sc, exit_fn, START, END, k_slots=K, max_new=MAX_NEW, max_hold=hold)
        res[name] = split(t, curve)
        r = res[name]
        print(name, " ".join(f"{p.split()[0]}:{r[p].get('n', 0)}/{r[p].get('avg', 0):+.2f}/pf{r[p].get('pf', 0)}"
                             for p in list(PHASES)[:8]), flush=True)
        if t is not None and t.height:
            t.write_csv(od / f"trades_{name}_N3.csv")
    Path(out_path).write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("done ->", out_path, flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
