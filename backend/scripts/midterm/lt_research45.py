"""[中线] §153 大盘弱势时不开新仓 — 预注册 3 版（docs/strategy-research.md §153，结果出来前写定，不调参）。
基线口径同 lt_research25：realistic 引擎、K=20、max_new=5、持 20 日、0.3% 成本。只拦新开仓，已持仓照常到期。
  G_ew20 : 全市场等权指数 < MA20 → 当天不开新仓
  G_csi20: 中证1000(000852.SH) < MA20 → 不开新仓
  G_ew60 : 全市场等权指数 < MA60 → 不开新仓
用法：python lt_research45.py "<phase>,<phase>" out.json"""
import sys, json, os
from pathlib import Path
import polars as pl
from lt_research import fixed, PH
from realistic import run_real, metrics_real
import lt_research25 as R25
c = pl.col
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
IDX = Path(__file__).resolve().parents[2] / "data/midterm/index_daily.parquet"
BASE = {"EA_h20": ("G", "sc_g"), "F_any_h20": ("FA", "sc_a"), "RP_r05": ("RP_r05", "sc_r"),
        "INC_opt_h20": ("IO", "sc_i"), "BT_prem_h20": ("BTP", "sc_bt")}
GUARDS = ("G_ew20", "G_ew60")  # G_csi20 推迟：中证1000 本地只有 2024 起，补长历史时 promax 熔断（§153 注）


def regime(df: pl.DataFrame) -> pl.DataFrame:
    ew = (df.group_by("trade_date").agg(c("pct_chg").clip(-20, 20).mean().alias("s")).sort("trade_date")
            .with_columns((1 + c("s").fill_null(0) / 100).cum_prod().alias("ew"))
            .with_columns(c("ew").rolling_mean(20).alias("ew20"), c("ew").rolling_mean(60).alias("ew60")))
    ix = pl.read_parquet(IDX).filter(c("ts_code") == "000852.SH")
    ix = (ix.with_columns(c("trade_date").cast(pl.Utf8).str.replace_all("-", "").alias("td8"))
            .with_columns((c("td8").str.slice(0, 4) + "-" + c("td8").str.slice(4, 2) + "-" + c("td8").str.slice(6, 2)).alias("trade_date"))
            .sort("trade_date").with_columns(c("close").cast(pl.Float64).rolling_mean(20).alias("csi20"))
            .select("trade_date", c("close").cast(pl.Float64).alias("csi"), "csi20"))
    r = ew.join(ix, on="trade_date", how="left").with_columns(
        (c("ew") < c("ew20")).fill_null(False).alias("G_ew20"),
        (c("csi") < c("csi20")).fill_null(False).alias("G_csi20"),
        (c("ew") < c("ew60")).fill_null(False).alias("G_ew60"))
    return r.select("trade_date", *GUARDS)


def prep():
    df = R25.prep()
    df = df.join(regime(df), on="trade_date", how="left")
    cols = []
    for n, (sig, _) in BASE.items():
        for g in GUARDS:
            cols.append((c(sig) & ~c(g).fill_null(False)).alias(f"{sig}__{g}"))
    return df.with_columns(cols)


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    reg = df.select("trade_date", *GUARDS).unique("trade_date")
    print("weak-day share by year:", reg.group_by(c("trade_date").str.slice(0, 4).alias("y"))
          .agg(*[c(g).mean().round(2) for g in GUARDS]).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        for n, (sig, sc) in BASE.items():
            for v in ("base", *GUARDS):
                col = sig if v == "base" else f"{sig}__{v}"
                t, cur = run_real(df, col, sc, fixed(20), s, e, k_slots=20, max_new=5, max_hold=20)
                m = metrics_real(t, cur)
                res.setdefault(n, {}).setdefault(v, {})[ph] = {a: float(x) for a, x in m.items()}
                print(n, v, ph, m, flush=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
