"""[长线] long-term research: low-vol dividend, forecast drift, quality-value. Realistic engine, PIT data."""
import sys, json
import polars as pl
from pathlib import Path
import lt_data as L
from realistic import add_limits, run_real, metrics_real

PH = {"P19 2019": ("2019-01-01", "2019-12-31"), "P20 2020": ("2020-01-01", "2020-12-31"),
      "P21 2021": ("2021-01-01", "2021-12-31"), "W0 2022-04~2023-03": ("2022-04-01", "2023-03-31"),
      "W00 2023-04~12": ("2023-04-01", "2023-12-31"), "W1 2024": ("2024-01-01", "2024-12-31"),
      "W2 2025-01~09": ("2025-01-01", "2025-09-30"), "R12 2025-10~2026-10": ("2025-10-01", "2026-10-16")}
c = pl.col
def fixed(n): return lambda a, e, i: i - e >= n - 1

def build():
    df = L.prices()
    df = df.with_columns(((1 + c("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code")).alias("ac"))
    df = df.with_columns((c("open") * c("ac") / c("close")).alias("ao"))
    df = df.with_columns(
        c("ac").rolling_mean(60).over("code").alias("m60"), c("ac").rolling_mean(250).over("code").alias("m250"),
        c("amount").rolling_mean(20).over("code").alias("amt20"),
        (c("pct_chg") / 100).rolling_std(120).over("code").alias("vol120"),
        (c("ac") / c("ac").shift(20).over("code") - 1).alias("r20"),
        (c("ac") / c("ac").shift(60).over("code") - 1).alias("r60"),
        pl.int_range(pl.len()).over("code").alias("age"))
    b = L.basic().with_columns([c(x).cast(pl.Float64, strict=False) for x in ("pe_ttm", "pb", "dv_ttm")]).sort("trade_date")
    df = df.sort("trade_date").join_asof(b.rename({"trade_date": "bd"}).with_columns(c("bd").alias("trade_date")),
                                         on="trade_date", by="code", strategy="backward")
    df = df.filter(c("bd").is_not_null())  # keep rows with basic within reach
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.join_asof(st, on="trade_date", by="code", strategy="backward").with_columns(c("st").fill_null(False))
    df = add_limits(df.sort("code", "trade_date"))
    d = c("trade_date").str.to_date()
    # first trading day of each month (signal at its close)
    df = df.with_columns(d.dt.strftime("%Y-%m").alias("ym"))
    fm = df.group_by("ym").agg(c("trade_date").min().alias("fm"))
    df = df.join(fm, on="ym").with_columns((c("trade_date") == c("fm")).alias("mon"))
    liq = (c("amt20") > 5e7) & (c("age") > 250) & ~c("st") & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
    df = df.with_columns(liq.fill_null(False).alias("liq"))
    return df.sort("code", "trade_date")

def forecast_sig(df):
    f = L.forecast_events().filter(c("type").is_in(["预增", "扭亏", "略增", "续盈"]))
    f = f.with_columns(c("p_change_min").cast(pl.Float64, strict=False)).sort("ann")
    td = df.select("trade_date").unique().sort("trade_date")
    f = f.join_asof(td.rename({"trade_date": "sd"}).with_columns(c("sd").alias("ann2")), left_on="ann", right_on="ann2", strategy="forward")
    # signal at close of first trade day >= ann (ann may be after close) -> buy next open
    f = f.group_by("code", "sd").agg(c("p_change_min").max().alias("fc_min"), c("type").first().alias("ftype"))
    return df.join(f.rename({"sd": "trade_date"}), on=["code", "trade_date"], how="left")

def signals(df):
    # (a) low-vol dividend: liquid, dv_ttm>=3, vol120 below cross-sectional median, rank by dv_ttm; monthly
    vr = pl.when(c("liq") & c("dv_ttm").is_not_null()).then(c("vol120")).rank().over("trade_date") / pl.when(c("liq") & c("dv_ttm").is_not_null()).then(1).sum().over("trade_date")
    df = df.with_columns(vr.alias("vrank"))
    divok = c("liq") & (c("dv_ttm") >= 3) & (c("vrank") <= 0.5) & (c("pe_ttm") > 0)
    df = df.with_columns(pl.when(divok).then(-c("dv_ttm")).rank("ordinal").over("trade_date").alias("rk_div"),
                         pl.when(divok & (c("ac") > c("m250"))).then(-c("dv_ttm")).rank("ordinal").over("trade_date").alias("rk_div_t"))
    # (c) quality-value: EP + BP rank, pe 0-20, pb>0
    qv = c("liq") & c("pe_ttm").is_between(0.1, 20) & (c("pb") > 0)
    ep = pl.when(qv).then(1 / c("pe_ttm")).rank().over("trade_date"); bp = pl.when(qv).then(1 / c("pb")).rank().over("trade_date")
    df = df.with_columns((ep + bp).alias("qvs"))
    df = df.with_columns(pl.when(qv).then(-c("qvs")).rank("ordinal").over("trade_date").alias("rk_qv"),
                         pl.when(qv & (c("r60") > 0)).then(-c("qvs")).rank("ordinal").over("trade_date").alias("rk_qvm"))
    fc = c("liq") & c("fc_min").is_not_null() & (c("r20") < 0.25)
    return df.with_columns(
        (c("mon") & (c("rk_div") <= 20)).fill_null(False).alias("S_div"),
        (c("mon") & (c("rk_div_t") <= 20)).fill_null(False).alias("S_div_t"),
        (c("mon") & (c("rk_qv") <= 20)).fill_null(False).alias("S_qv"),
        (c("mon") & (c("rk_qvm") <= 20)).fill_null(False).alias("S_qvm"),
        (fc & (c("fc_min") >= 50)).fill_null(False).alias("S_fc50"),
        (fc & (c("fc_min") >= 100)).fill_null(False).alias("S_fc100"),
        (fc & (c("fc_min") >= 50) & (c("ac") > c("m60"))).fill_null(False).alias("S_fc50t"),
        (-c("vrank")).alias("sc_lv"), c("dv_ttm").alias("sc_dv"), c("qvs").alias("sc_qv"), c("fc_min").fill_null(0).alias("sc_fc"))

C = {  # sig, score, hold, K, max_new
  "div_lowvol_m": ("S_div", "sc_dv", 20, 20, 20), "div_lowvol_trend_m": ("S_div_t", "sc_dv", 20, 20, 20),
  "qv_m": ("S_qv", "sc_qv", 20, 20, 20), "qv_mom_m": ("S_qvm", "sc_qv", 20, 20, 20),
  "fc50_h20": ("S_fc50", "sc_fc", 20, 20, 5), "fc100_h20": ("S_fc100", "sc_fc", 20, 20, 5),
  "fc50_h40": ("S_fc50", "sc_fc", 40, 20, 5), "fc50t_h20": ("S_fc50t", "sc_fc", 20, 20, 5),
}
if __name__ == "__main__":
    df = signals(forecast_sig(build())); print("built", df.height, flush=True)
    res = {}
    for n in (sys.argv[1:] or C):
        sig, sc, h, k, mn = C[n]; print(f"## {n}", flush=True)
        for ph, (s, e) in PH.items():
            t, cur = run_real(df, sig, sc, fixed(h), s, e, k_slots=k, max_new=mn, max_hold=h)
            m = metrics_real(t, cur); res.setdefault(n, {})[ph] = {a: float(v) for a, v in m.items()}
            print(f"  {ph:22s} " + " ".join(f"{a}={float(v):g}" for a, v in m.items()), flush=True)
    json.dump(res, open("/tmp/lt1.json", "w"), indent=1)
