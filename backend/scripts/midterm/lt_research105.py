"""[中线] §281「长期横盘后向上异动」（BOX*，邢不行精华帖《【形态库】横盘异动信号的描述》，持20日） — 预注册（docs/strategy-research.md §281，结果出来前写定，不调参）。
口径同 lt_research103：realistic 引擎、liq、K=20、max_new=5（仅回测）、cohort_bench 同日同市值五分位对照。
  横盘箱体（截至 T−1 的 200 个交易日复权收盘 ac）：m = 中位数；箱体成立 = 5% 分位 ≥ 0.85m 且 95% 分位 ≤ 1.15m（≥90% 的日子在中位数 ±15% 内）。
  BOX：liq & 箱体成立 & ac_T > 1.15m & ac_{T−1} ≤ 1.15m（首次收盘突破箱体上沿）。BOXv：再要求 amount_T ≥ 2×前 20 日均成交额（放量异动）。
  排序分 = min(amount_T / 前 20 日均额, 20) 降序。只持 20 日（长线暂停）。
用法：LT_START=<phase start> LT_END=<phase end+buffer> PYTHONPATH=../..:. python lt_research105.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
W, BAND = 200, 0.15


def prep():
    df = build().sort("code", "trade_date")
    p = c("ac").shift(1).over("code")
    df = df.with_columns(
        p.rolling_median(window_size=W).over("code").alias("bm"),
        p.rolling_quantile(quantile=0.05, interpolation="linear", window_size=W).over("code").alias("bq05"),
        p.rolling_quantile(quantile=0.95, interpolation="linear", window_size=W).over("code").alias("bq95"),
        c("amount").shift(1).rolling_mean(window_size=20).over("code").alias("am20p"))
    box = (c("bq05") >= (1 - BAND) * c("bm")) & (c("bq95") <= (1 + BAND) * c("bm"))
    up = c("bm") * (1 + BAND)
    brk = (c("ac") > up) & (c("ac").shift(1).over("code") <= up)
    vr = c("amount") / c("am20p")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    return df.with_columns(
        (c("liq") & box & brk).fill_null(False).alias("BOX"),
        (c("liq") & box & brk & (vr >= 2)).fill_null(False).alias("BOXv"),
        (pl.min_horizontal(vr, pl.lit(20.0)).fill_null(0) - tie).alias("sc_box"),
    ).drop("bm", "bq05", "bq95", "am20p").sort("code", "trade_date")


NEW = {"BOX_h20": ("BOX", "sc_box", 20), "BOXv_h20": ("BOXv", "sc_box", 20)}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(c("BOX").sum(), c("BOXv").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        for n, (sig, sc, h) in NEW.items():
            t, cur = run_real(df, sig, sc, fixed(h), s, e, k_slots=20, max_new=5, max_hold=h)
            m = full_metrics(df, t, cur)
            if t is not None and t.height:
                k = mx = 0
                for x in t.sort("entry")["ret"].to_list():
                    k = k + 1 if x <= 0 else 0; mx = max(mx, k)
                m["streak"] = float(mx); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
                if ph.startswith(("R12", "N3")):
                    t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{n}_{ph.split()[0]}.csv"))
            res.setdefault(n, {})[ph] = m
            print(n, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
