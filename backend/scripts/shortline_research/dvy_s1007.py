"""[短线研究 §1007–§1009] 第二批：「深跌后技术确认」替代「深跌后事件催化」。
预注册 docs/strategy-research.md §1007–§1009（规则写死后提交，再跑任何收益；不调参）。

核心洞察：§993/§997/§1000 证明 r20≤−15% 的深跌提供安全边际，公司事件提供催化。
本轮用技术确认信号替代公司事件，检验「深跌 + 技术企稳」是否也能产生正收益。

§1007 DVY_h5「深跌后首次放量阳线」：r20≤−15% + 今日量≥2×20日均量 + 收阳。
  去重：§193 LL20（贴近20日低点+阳线，无r20门槛无量能）；§200 VCD（单日大跌+放量，方向相反）；
  §165 CDD（连跌，无量能/阳线要求）；§332 LVZ（低波股异常下跌，非累计深跌）。

§1008 D2Y_h5「深跌后连续两日收阳」：r20(T-2)≤−15% + T-1收阳 + T收阳且T收>T-1收。
  去重：§292 三白兵（无深跌前提）；§165 CDD（连跌不是连涨）；§1007（单日确认不是两日）。

§1009 SDS_h5「深跌×行业热点×企稳」：r20≤−15% + 行业5日收益前5 + 今日振幅≤5%且收阳。
  去重：§1004 SEC_DIP（r20≤−10%、无企稳确认、行业前3）；L-179 板块补涨（无深跌）。

执行：realistic 引擎，T+1 开盘买（一字涨停买不进），持 5 日或跌破入场×0.93 止损，
K=20、max_new=5（同 §993），往返 0.3%。
用法：cd backend/scripts/midterm && PYTHONPATH=../..:.:../shortline_research ../../.venv/bin/python ../shortline_research/dvy_s1007.py out.json
"""
import sys, json, os
from pathlib import Path
import numpy as np
import polars as pl
import lt_data as L
from realistic import add_limits, run_real
from lt_research import PH

c = pl.col
MAX_NEW, K, HOLD, STOP = 5, 20, 5, 0.93
START, END = "2019-01-01", "2026-10-16"
R20_DIP = -0.15

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
        # for §1008: previous day's close vs open (yang check)
        (c("close").shift(1).over("code")).alias("prev_close"),
        (c("open").shift(1).over("code")).alias("prev_open"),
        # r20 at T-2 for §1008
        (c("ac").shift(2).over("code") / c("ac").shift(22).over("code") - 1).alias("r20_t2"),
        # range pct for §1009
        ((c("high") - c("low")) / c("close").shift(1).over("code")).alias("range_pct"),
    )
    # ST
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.sort("trade_date").join_asof(st, on="trade_date", by="code", strategy="backward").with_columns(c("st").fill_null(False))
    df = add_limits(df.sort("code", "trade_date"))

    # limit-up flag
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    pc = c("close").shift(1).over("code")
    ul = pl.coalesce(c("up_limit"), pc * (1 + band))
    df = df.with_columns((c("close") >= ul - 0.005).fill_null(False).alias("is_lu"))

    # industry
    ind = json.loads(IND_CACHE.read_text())
    df = df.with_columns(c("code").replace_strict(ind, default=None).alias("industry"))

    # universe (liq: amt20>5000万, age>250, non-ST, non-BSE — same as §993)
    amt20 = c("amount").rolling_mean(20).over("code")
    df = df.with_columns(amt20.alias("amt20"))
    liq = (c("amt20") > 5e7) & (c("age") > 250) & ~c("st") & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
    df = df.with_columns(liq.fill_null(False).alias("liq"))

    # ── §1009: industry 5-day return ranking ──
    ind_r5 = (df.filter(c("liq") & c("industry").is_not_null() & c("r5").is_not_null())
              .group_by(["trade_date", "industry"])
              .agg(c("r5").mean().alias("ind_r5"), pl.len().alias("ind_n"))
              .filter(c("ind_n") >= 5))
    ind_r5 = ind_r5.with_columns(c("ind_r5").rank(descending=True).over("trade_date").alias("ind_rank"))
    df = df.join(ind_r5.select("trade_date", "industry", "ind_r5", "ind_rank"), on=["trade_date", "industry"], how="left")

    # ── Signals ──
    yang = c("close") > c("open")
    not_lu = ~c("is_lu")

    # §1007 DVY: deep decline + volume expansion + yang
    sig_1007 = c("liq") & (c("r20") <= R20_DIP) & (c("vr20") >= 2.0) & yang & not_lu
    # CTRL_1007: deep decline + yang but NO volume expansion (vr20 < 1.5)
    ctrl_1007 = c("liq") & (c("r20") <= R20_DIP) & (c("vr20") < 1.5) & yang & not_lu

    # §1008 D2Y: r20 at T-2 <= -15% + prev day yang + today yang + today close > prev close
    prev_yang = c("prev_close") > c("prev_open")
    sig_1008 = (c("liq") & (c("r20_t2") <= R20_DIP) & prev_yang & yang
                & (c("close") > c("prev_close")) & not_lu)
    # CTRL_1008: same deep decline + today yang but prev day NOT yang
    ctrl_1008 = (c("liq") & (c("r20_t2") <= R20_DIP) & ~prev_yang & yang
                 & (c("close") > c("prev_close")) & not_lu)

    # §1009 SDS: deep decline + industry top 5 + stabilization (range <= 5% + yang)
    sig_1009 = (c("liq") & (c("r20") <= R20_DIP) & (c("ind_rank") <= 5)
                & (c("range_pct").fill_null(9) <= 0.05) & yang & not_lu)
    # CTRL_1009: deep decline + industry top 5 but NO stabilization (range > 5% or yin)
    ctrl_1009 = (c("liq") & (c("r20") <= R20_DIP) & (c("ind_rank") <= 5)
                 & ((c("range_pct").fill_null(9) > 0.05) | ~yang) & not_lu)

    # scores
    df = df.with_columns(
        sig_1007.fill_null(False).alias("sig_DVY"),
        ctrl_1007.fill_null(False).alias("sig_CTRL_1007"),
        sig_1008.fill_null(False).alias("sig_D2Y"),
        ctrl_1008.fill_null(False).alias("sig_CTRL_1008"),
        sig_1009.fill_null(False).alias("sig_SDS"),
        ctrl_1009.fill_null(False).alias("sig_CTRL_1009"),
        (-c("r20").fill_null(-9)).alias("sc_dip"),  # deeper decline = higher score
        (pl.concat_str([c("code"), c("trade_date")]).hash(1007) % 1000003).cast(pl.Float64).alias("sc_rand"),
    )
    keep = ["code", "trade_date", "ao", "ac", "lim_up_open", "lock_down",
            "sig_DVY", "sig_CTRL_1007", "sig_D2Y", "sig_CTRL_1008", "sig_SDS", "sig_CTRL_1009",
            "sc_dip", "sc_rand"]
    return df.select(keep)


def ex(a, e, i):
    return a["ac"][i] < a["ao"][e] * STOP or i - e >= HOLD - 1


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
    for s in ("sig_DVY", "sig_D2Y", "sig_SDS"):
        cnt = df.filter(c(s)).group_by(yr).len().sort("y")
        print(f"{s}/yr:", cnt.to_dicts(), flush=True)

    od = Path(out_path).parent
    od.mkdir(parents=True, exist_ok=True)
    res = {}
    configs = [
        ("DVY_h5", "sig_DVY", "sc_dip", ex),
        ("CTRL_1007", "sig_CTRL_1007", "sc_rand", ex),
        ("D2Y_h5", "sig_D2Y", "sc_dip", ex),
        ("CTRL_1008", "sig_CTRL_1008", "sc_rand", ex),
        ("SDS_h5", "sig_SDS", "sc_dip", ex),
        ("CTRL_1009", "sig_CTRL_1009", "sc_rand", ex),
    ]
    for name, sig, sc, exit_fn in configs:
        t, curve = run_real(df, sig, sc, exit_fn, START, END, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
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
