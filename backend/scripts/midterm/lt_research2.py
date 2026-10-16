"""[长线] round 2: regime-gated variants of forecast drift / dividend / QV."""
import sys, json
import polars as pl
from lt_research import build, forecast_sig, signals, fixed, PH, C as C1
from realistic import run_real, metrics_real
c = pl.col
def regime(df):
    ew = (df.filter(c("liq")).group_by("trade_date").agg(c("pct_chg").clip(-20, 20).mean().alias("er")).sort("trade_date")
          .with_columns((1 + c("er") / 100).cum_prod().alias("ewi")))
    ew = ew.with_columns(c("ewi").rolling_mean(20).alias("e20"), c("ewi").rolling_mean(60).alias("e60"))
    return df.join(ew.select("trade_date", "ewi", "e20", "e60"), on="trade_date", how="left").sort("code", "trade_date")
if __name__ == "__main__":
    df = regime(signals(forecast_sig(build())))
    g20 = c("ewi") > c("e20"); g60 = c("ewi") > c("e60")
    small = c("total_mv") < 1.5e6  # <150亿 (万元)
    early = c("ftype").is_in(["预增", "扭亏"])
    df = df.with_columns(
        (c("S_fc50") & g20).fill_null(False).alias("F_g20"),
        (c("S_fc50") & g60).fill_null(False).alias("F_g60"),
        (c("S_fc50") & g20 & small).fill_null(False).alias("F_g20s"),
        (c("S_fc50") & g20 & (c("r20") < 0.10) & (c("pe_ttm").is_between(0, 40))).fill_null(False).alias("F_g20v"),
        (c("S_div") & g60).fill_null(False).alias("D_g60"),
        (c("S_qv") & g60).fill_null(False).alias("Q_g60"),
    )
    G = {"G_out20": ~g20, "G_out60": ~g60}
    df = df.with_columns([e.fill_null(False).alias(k) for k, e in G.items()])
    C = {  # sig score hold K maxnew guard
      "fc_g20_h20": ("F_g20", "sc_fc", 20, 20, 5, None), "fc_g20_h40": ("F_g20", "sc_fc", 40, 20, 5, None),
      "fc_g20_h40_x": ("F_g20", "sc_fc", 40, 20, 5, "G_out20"), "fc_g60_h40": ("F_g60", "sc_fc", 40, 20, 5, None),
      "fc_g20s_h40": ("F_g20s", "sc_fc", 40, 20, 5, None), "fc_g20v_h40": ("F_g20v", "sc_fc", 40, 20, 5, None),
      "fc_g20v_h20": ("F_g20v", "sc_fc", 20, 20, 5, None),
      "div_g60_h20": ("D_g60", "sc_dv", 20, 20, 20, None), "div_g60_h20_x": ("D_g60", "sc_dv", 20, 20, 20, "G_out60"),
      "qv_g60_h20": ("Q_g60", "sc_qv", 20, 20, 20, None),
    }
    res = {}
    for n in (sys.argv[1:] or C):
        sig, sc, h, k, mn, g = C[n]; print(f"## {n}", flush=True)
        for ph, (s, e) in PH.items():
            t, cur = run_real(df, sig, sc, fixed(h), s, e, k_slots=k, max_new=mn, max_hold=h, guard_col=g)
            m = metrics_real(t, cur); res.setdefault(n, {})[ph] = {a: float(v) for a, v in m.items()}
            print(f"  {ph:22s} " + " ".join(f"{a}={float(v):g}" for a, v in m.items()), flush=True)
    json.dump(res, open("/tmp/lt2.json", "w"), indent=1)
