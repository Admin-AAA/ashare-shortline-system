"""[短线研究 §1022–§1024] 事件/行业催化 × 企稳确认的密度修复批（预注册 docs/strategy-research.md §1022–§1024，
预注册 commit ac0019dd，规则写死后开跑，不调参）。

§1022 HNS2_h5    ：股东户数环比≤−10%（PIT）× 振幅≤5%收阳 × vr20≥1.0（**无深跌条件**）。CTRL: 纯事件基线。
§1023 MDIP_h5    ：行业前5 × r20∈(−15%,−8%] 中度跌幅带 × 振幅≤5%收阳 × vr20≥1.0。CTRL: 同带同行业无确认。
§1024 HOTSTAB_h5 ：行业前5 × r20>−8%（无跌幅）× 振幅≤5%收阳 × vr20≥1.0。CTRL: 同热点无确认。
REF_1011         ：逐字复现 sds_s1010.py SDS_VOL_h5（行业前5×r20≤−15%×振幅≤5%收阳×vr20≥1.0），
                   只用于重叠检查，不参与采纳。

执行：realistic 引擎，liq（amt20>5000万、上市>250日、非ST、非北交所），T+1 开盘买（一字涨停买不进），
持 5 日或收盘跌破入场开盘价×0.93 止损，K=20、max_new=5，往返 0.3%。
逐阶段独立 run_real（同 §996 批次口径）；指标 = metrics_real + cohort_bench 同市值五分位（bench/excess 只展示）。
用法：cd backend/scripts/midterm && PYTHONPATH=../..:.:../shortline_research ../../.venv/bin/python \
      ../shortline_research/dvys_s1022.py "<phase1,phase2,...>" out.json
"""
import sys, json, os
from pathlib import Path
import numpy as np
import polars as pl
import lt_data as L
from realistic import add_limits, run_real, metrics_real, COST
from lt_research import PH
from lt_research6 import holder_events

c = pl.col
K, MAX_NEW, HOLD, STOP = 20, 5, 5, 0.93
START, END = "2019-01-01", "2026-10-16"

PHASES = dict(PH)
PHASES["EXT 2024-01~2025-09"] = ("2024-01-01", "2025-09-30")
PHASES["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
PHASES["FULL 2019-01~2026-10"] = ("2019-01-01", "2026-10-16")

IND_CACHE = Path(__file__).resolve().parents[2] / "data/industry_cache.json"

VERSIONS = [
    ("HNS2_h5",     "sig_S_HNS2",    "sc_hn"),
    ("CTRL_HNS2",   "sig_C_HNS2",    "sc_rand"),
    ("MDIP_h5",     "sig_S_MDIP",    "sc_dip20"),
    ("CTRL_MDIP",   "sig_C_MDIP",    "sc_rand"),
    ("HOTSTAB_h5",  "sig_S_HOTSTAB", "sc_dip20"),
    ("CTRL_HOTSTAB","sig_C_HOTSTAB", "sc_rand"),
    ("REF_1011",    "sig_REF",       "sc_dip20_notie"),
]


def ex5(a, e, i):
    return a["ac"][i] < a["ao"][e] * STOP or i - e >= HOLD - 1


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0
        mx = max(mx, k)
    return mx


def panel():
    df = L.prices(START, END)
    df = df.with_columns(((1 + c("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code")).alias("ac"))
    kf = c("ac") / c("close")
    df = df.with_columns(
        (c("open") * kf).alias("ao"),
        pl.int_range(pl.len()).over("code").alias("age"),
        (c("ac") / c("ac").shift(20).over("code") - 1).alias("r20"),
        (c("ac") / c("ac").shift(5).over("code") - 1).alias("r5"),
        (c("ac") / c("ac").shift(60).over("code") - 1).alias("r60"),
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

    # total_mv（cohort_bench 五分位用；周度快照 join_asof backward，同 lt_research.build）
    b = pl.concat([pl.read_parquet(f, columns=["ts_code", "trade_date", "total_mv"])
                   for f in sorted(L.D.glob("basic_[0-9]*.parquet"))], how="diagonal_relaxed")
    b = b.select(c("ts_code").str.slice(0, 6).alias("code"),
                 (pl.concat_str([c("trade_date").str.slice(0, 4), c("trade_date").str.slice(4, 2),
                                 c("trade_date").str.slice(6, 2)], separator="-")).alias("bd"),
                 c("total_mv").cast(pl.Float64, strict=False)).unique(["code", "bd"]).sort("bd")
    df = df.sort("trade_date").join_asof(b, left_on="trade_date", right_on="bd", by="code", strategy="backward")

    # industry + 行业5日等权收益排名（复现 sds_s1010 口径）
    ind = json.loads(IND_CACHE.read_text())
    df = df.with_columns(c("code").replace_strict(ind, default=None).alias("industry"))
    liq = (c("amt20") > 5e7) & (c("age") > 250) & ~c("st") & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
    df = df.with_columns(liq.fill_null(False).alias("liq"))
    ind_r5 = (df.filter(c("liq") & c("industry").is_not_null() & c("r5").is_not_null())
              .group_by(["trade_date", "industry"])
              .agg(c("r5").mean().alias("ind_r5"), pl.len().alias("ind_n"))
              .filter(c("ind_n") >= 5))
    ind_r5 = ind_r5.with_columns(c("ind_r5").rank(descending=True).over("trade_date").alias("ind_rank"))
    df = df.join(ind_r5.select("trade_date", "industry", "ind_rank"), on=["trade_date", "industry"], how="left")

    # 股东户数事件（PIT，lt_research6 口径）
    td = df.select("trade_date").unique().sort("trade_date")
    ev = holder_events(td)
    df = df.join(ev.select("code", "trade_date", "hn_chg"), on=["code", "trade_date"], how="left")

    # cohort_bench 预计算：同市值五分位（liq & total_mv 非空子集内按日排名，同 lt_research6_ctrl.cohort_bench）
    qsub = (df.filter(c("liq") & c("total_mv").is_not_null())
            .with_columns((c("total_mv").rank().over("trade_date") * 5 / pl.len().over("trade_date")).ceil().clip(1, 5).alias("q5"))
            .select("code", "trade_date", "q5"))
    bench_frames = {
        "td_prev": td.with_columns(c("trade_date").shift(1).alias("sig")),
        "q5": qsub,
        "px": df.select("code", "trade_date", "ao"),
    }

    # ── 信号（预注册写死）──
    yang = c("close") > c("open")
    stable = c("range_pct").fill_null(9) <= 0.05
    not_lu = ~c("is_lu")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    dip20 = c("liq") & (c("r20") <= -0.15)
    band = c("liq") & (c("r20") > -0.15) & (c("r20") <= -0.08)
    nodip = c("liq") & (c("r20") > -0.08)
    hot = c("ind_rank") <= 5
    hn_ev = c("liq") & (c("hn_chg") <= -0.10)
    confirm = stable & yang & (c("vr20") >= 1.0)
    df = df.with_columns(
        (hn_ev & confirm & not_lu).fill_null(False).alias("sig_S_HNS2"),
        (hn_ev & not_lu).fill_null(False).alias("sig_C_HNS2"),
        (hot & band & confirm & not_lu).fill_null(False).alias("sig_S_MDIP"),
        (hot & band & (~stable | ~yang) & not_lu).fill_null(False).alias("sig_C_MDIP"),
        (hot & nodip & confirm & not_lu).fill_null(False).alias("sig_S_HOTSTAB"),
        (hot & nodip & not_lu).fill_null(False).alias("sig_C_HOTSTAB"),
        (dip20 & hot & confirm & not_lu).fill_null(False).alias("sig_REF"),
        (-c("r20").fill_null(-9) - tie).alias("sc_dip20"),
        (-c("r20").fill_null(-9)).alias("sc_dip20_notie"),
        (-c("hn_chg").fill_null(-9) - tie).alias("sc_hn"),
        (pl.concat_str([c("code"), c("trade_date")]).hash(1022) % 1000003).cast(pl.Float64).alias("sc_rand"),
    )
    keep = ["code", "trade_date", "ao", "ac", "lim_up_open", "lock_down"] + \
           [s for _, s, _ in VERSIONS] + ["sc_dip20", "sc_dip20_notie", "sc_hn", "sc_rand"]
    return df.select(keep).sort("code", "trade_date"), bench_frames, df.select("code", "trade_date", *[s for _, s, _ in VERSIONS])


def bench_fast(bf, t):
    """cohort_bench 等价实现，q5/px/td_prev 预计算（lt_research6_ctrl.cohort_bench 同口径）。"""
    t = (t.with_row_index("tid")
         .join(bf["td_prev"].rename({"trade_date": "entry"}), on="entry", how="left")
         .join(bf["q5"].rename({"trade_date": "sig"}), on=["code", "sig"], how="left"))
    coh = (t.select("tid", "sig", "q5", "entry", "exit")
           .join(bf["q5"].rename({"trade_date": "sig", "code": "m"}), on=["sig", "q5"])
           .join(bf["px"].rename({"trade_date": "entry", "code": "m", "ao": "a0"}), on=["m", "entry"])
           .join(bf["px"].rename({"trade_date": "exit", "code": "m", "ao": "a1"}), on=["m", "exit"])
           .group_by("tid").agg((c("a1") / c("a0") - 1 - COST).clip(-0.9, 5).mean().alias("bench"), pl.len().alias("nc")))
    return t.join(coh, on="tid", how="left")


def main(phases_arg, out_path):
    phases = [p.strip() for p in phases_arg.split(",")]
    for p in phases:
        assert p in PHASES, f"unknown phase {p}"
    df, bf, sig_df = panel()
    print("panel", df.shape, flush=True)
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals/yr:", sig_df.filter(c("trade_date") >= START).group_by(yr)
          .agg(*[c(x).sum() for x in sig_df.columns if x.startswith("sig_")]).sort("y").to_dicts(), flush=True)
    del sig_df

    od = Path(out_path).parent
    od.mkdir(parents=True, exist_ok=True)
    res = json.load(open(out_path)) if os.path.exists(out_path) else {}
    for ph in phases:
        s, e = PHASES[ph]
        sub = df if s == START else df.filter(c("trade_date") >= s)
        for name, sig, sc in VERSIONS:
            t, cur = run_real(sub, sig, sc, ex5, s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
            m = metrics_real(t, cur)
            if t is not None and t.height:
                b = bench_fast(bf, t)
                m["bench"] = round(100 * float(b["bench"].mean()), 2)
                m["excess"] = round(m["avg"] - m["bench"], 2)
                m["ex_win"] = round(100 * float(((b["ret"] - b["bench"]) > 0).mean()), 1)
                m["streak"] = float(streak(t))
                m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
                t.write_csv(od / f"trades_{name}_{ph.split()[0]}.csv")
            m = {a: (float(x) if isinstance(x, (int, float, np.floating)) else x) for a, x in m.items()}
            res.setdefault(name, {})[ph] = m
            print(name, ph, m, flush=True)
            json.dump(res, open(out_path, "w"), indent=1, ensure_ascii=False)
        if sub is not df:
            del sub
    print("done", out_path, flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
