"""[短线研究 §1010–§1012] 第三批：SDS 结构变体（更深跌幅 / 加量能 / 缩短持有）。
预注册 docs/strategy-research.md §1010–§1012（规则写死后提交，再跑任何收益；不调参）。

背景：§1009 SDS_h5（r20≤−15% + 行业前5 + 振幅≤5%收阳）6/8正、4/4正、回撤浅，
但 PF 1.05/1.24 未达 1.3。本轮测试三个独立变体，看哪个能推 PF 过线。

§1010 SDS_DEEP_h5：r20≤−20%（更深跌幅）+ 行业前5 + 振幅≤5%收阳。
  假设：更深跌幅 = 更大安全边际 = 更高盈亏比。
§1011 SDS_VOL_h5：r20≤−15% + 行业前5 + 振幅≤5%收阳 + vr20≥1.0（量能确认）。
  假设：企稳日有正常量能 = 真实承接而非无量空涨。
§1012 SDS_H3：r20≤−15% + 行业前5 + 振幅≤5%收阳，持 3 日（缩短持有）。
  假设：反弹初段收益最集中，持 5 日后半段回吐。

去重：§1009 SDS_h5（r20≤−15%、行业前5、振幅≤5%收阳、持5）已否（PF不达标）；
§1004 SEC_DIP（r20≤−10%、行业前3、无企稳）已否；L-179 板块补涨（无深跌）已删。
本轮三个变体各自独立预注册，与 §1009 的区别分别是：更深阈值 / 加量能条件 / 缩短持有期。

执行：realistic，liq（amt20>5000万、上市>250日、非ST、非北交所），T+1 开盘买，
持 5 日（§1012 持 3 日）或跌破入场×0.93 止损，K=20、max_new=5，0.3% 成本。
用法：cd backend/scripts/midterm && PYTHONPATH=../..:.:../shortline_research ../../.venv/bin/python ../shortline_research/sds_s1010.py out.json
"""
import sys, json, os
from pathlib import Path
import numpy as np
import polars as pl
import lt_data as L
from realistic import add_limits, run_real
from lt_research import PH

c = pl.col
MAX_NEW, K, STOP = 5, 20, 0.93
START, END = "2019-01-01", "2026-10-16"

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
        (c("ac") / c("ac").shift(20).over("code") - 1).alias("r20"),
        (c("ac") / c("ac").shift(5).over("code") - 1).alias("r5"),
        (c("volume") / c("volume").shift(1).rolling_mean(20).over("code")).alias("vr20"),
        ((c("high") - c("low")) / c("close").shift(1).over("code")).alias("range_pct"),
        c("amount").rolling_mean(20).over("code").alias("amt20"),
    )
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.sort("trade_date").join_asof(st, on="trade_date", by="code", strategy="backward").with_columns(c("st").fill_null(False))
    df = add_limits(df.sort("code", "trade_date"))

    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    pc = c("close").shift(1).over("code")
    ul = pl.coalesce(c("up_limit"), pc * (1 + band))
    df = df.with_columns((c("close") >= ul - 0.005).fill_null(False).alias("is_lu"))

    ind = json.loads(IND_CACHE.read_text())
    df = df.with_columns(c("code").replace_strict(ind, default=None).alias("industry"))

    liq = (c("amt20") > 5e7) & (c("age") > 250) & ~c("st") & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
    df = df.with_columns(liq.fill_null(False).alias("liq"))

    # industry 5-day return ranking
    ind_r5 = (df.filter(c("liq") & c("industry").is_not_null() & c("r5").is_not_null())
              .group_by(["trade_date", "industry"])
              .agg(c("r5").mean().alias("ind_r5"), pl.len().alias("ind_n"))
              .filter(c("ind_n") >= 5))
    ind_r5 = ind_r5.with_columns(c("ind_r5").rank(descending=True).over("trade_date").alias("ind_rank"))
    df = df.join(ind_r5.select("trade_date", "industry", "ind_r5", "ind_rank"), on=["trade_date", "industry"], how="left")

    yang = c("close") > c("open")
    not_lu = ~c("is_lu")
    stable = c("range_pct").fill_null(9) <= 0.05

    # §1010 SDS_DEEP: r20 <= -20% + industry top 5 + stable + yang
    sig_1010 = c("liq") & (c("r20") <= -0.20) & (c("ind_rank") <= 5) & stable & yang & not_lu
    # §1011 SDS_VOL: r20 <= -15% + industry top 5 + stable + yang + vr20 >= 1.0
    sig_1011 = c("liq") & (c("r20") <= -0.15) & (c("ind_rank") <= 5) & stable & yang & not_lu & (c("vr20") >= 1.0)
    # §1012 SDS_H3: r20 <= -15% + industry top 5 + stable + yang (hold 3)
    sig_1012 = c("liq") & (c("r20") <= -0.15) & (c("ind_rank") <= 5) & stable & yang & not_lu

    # CTRL for each: same deep decline + industry top 5 but NO stabilization
    ctrl = c("liq") & (c("r20") <= -0.15) & (c("ind_rank") <= 5) & (~stable | ~yang) & not_lu

    df = df.with_columns(
        sig_1010.fill_null(False).alias("sig_SDS_DEEP"),
        sig_1011.fill_null(False).alias("sig_SDS_VOL"),
        sig_1012.fill_null(False).alias("sig_SDS_H3"),
        ctrl.fill_null(False).alias("sig_CTRL"),
        (-c("r20").fill_null(-9)).alias("sc_dip"),
        (pl.concat_str([c("code"), c("trade_date")]).hash(1010) % 1000003).cast(pl.Float64).alias("sc_rand"),
    )
    keep = ["code", "trade_date", "ao", "ac", "lim_up_open", "lock_down",
            "sig_SDS_DEEP", "sig_SDS_VOL", "sig_SDS_H3", "sig_CTRL", "sc_dip", "sc_rand"]
    return df.select(keep)


def ex5(a, e, i):
    return a["ac"][i] < a["ao"][e] * STOP or i - e >= 4

def ex3(a, e, i):
    return a["ac"][i] < a["ao"][e] * STOP or i - e >= 2


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
    yr = c("trade_date").str.slice(0, 4).alias("y")
    for s in ("sig_SDS_DEEP", "sig_SDS_VOL", "sig_SDS_H3"):
        cnt = df.filter(c(s)).group_by(yr).len().sort("y")
        print(f"{s}/yr:", cnt.to_dicts(), flush=True)

    od = Path(out_path).parent
    od.mkdir(parents=True, exist_ok=True)
    res = {}
    configs = [
        ("SDS_DEEP_h5", "sig_SDS_DEEP", "sc_dip", ex5, 5),
        ("SDS_VOL_h5", "sig_SDS_VOL", "sc_dip", ex5, 5),
        ("SDS_H3", "sig_SDS_H3", "sc_dip", ex3, 3),
        ("CTRL_1010", "sig_CTRL", "sc_rand", ex5, 5),
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
