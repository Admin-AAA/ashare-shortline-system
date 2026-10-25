"""[中线] §271「低位放量」事件（LV*，邢不行文章，持20日） — 预注册（docs/strategy-research.md §271，结果出来前写定，不调参）。
口径同 lt_research51~102：realistic 引擎、liq、K=20、max_new=5（仅回测）、cohort_bench 同日同市值五分位对照。
  低位放量：ac_T < rolling_quantile(ac,0.1,120) 且 amount_T > rolling_mean(amount,120)+1.5*rolling_std(amount,120)（均含 T，需满 120 日）。
  量能用成交额 amount（防送转假放量）。LV：liq & 低位放量；LVr：再要求 pct_chg>0。排序分 = min(z,10) 降序。只持 20 日（长线暂停）。
用法：LT_START=<phase start> LT_END=<phase end+buffer> PYTHONPATH=../..:. python lt_research103.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
import lt_research25 as R25
c = pl.col


def prep():
    df = R25.prep().sort("code", "trade_date")
    q10 = c("ac").rolling_quantile(quantile=0.1, interpolation="linear", window_size=120).over("code")
    am = c("amount").rolling_mean(window_size=120).over("code")
    sd = c("amount").rolling_std(window_size=120).over("code")
    df = df.with_columns(q10.alias("q10"), am.alias("am120"), sd.alias("sd120"))
    z = ((c("amount") - c("am120")) / c("sd120"))
    lv = (c("ac") < c("q10")) & (c("amount") > c("am120") + 1.5 * c("sd120"))
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    return df.with_columns(
        (c("liq") & lv).fill_null(False).alias("LV"),
        (c("liq") & lv & (c("pct_chg") > 0)).fill_null(False).alias("LVr"),
        (pl.min_horizontal(z, pl.lit(10.0)).fill_null(0) - tie).alias("sc_lv"),
    ).drop("q10", "am120", "sd120").sort("code", "trade_date")


NEW = {
    "LV_h20": ("LV", "sc_lv", 20),
    "LVr_h20": ("LVr", "sc_lv", 20),
}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(c("LV").sum(), c("LVr").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        for n, (sig, sc, h) in NEW.items():
            t, cur = run_real(df, sig, sc, fixed(h), s, e, k_slots=20, max_new=5, max_hold=h)
            res.setdefault(n, {})[ph] = full_metrics(df, t, cur)
            print(n, ph, res[n][ph], flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
