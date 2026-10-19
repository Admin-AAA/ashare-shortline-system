"""[中线/长线] §114 大宗交易溢价成交后漂移 — 预注册 3 版（/workspace/strategy-research.md §114，结果出来前写定，不调参）。
数据：output/research/sprint4/block_trade/YYYYMMDD.parquet（scripts/midterm/fetch_block_trade_cache.py，统一数据网关 tushare block_trade 按交易日，只读，不写库）。
事件：每个 (code, T) 汇总当日全部大宗成交：amt = Σamount（万元），vwp = Σamount/Σvol（元/股）；
  premium = vwp / close_T − 1（不复权收盘）；fmv_T = 最近 daily_basic 快照 circ_mv（万元）× close_T / 快照日 close；
  bt_ratio = amt / fmv_T。PIT：T 收盘后出信号，T+1 开盘买（run_real：开盘涨停不买、跌停锁死顺延、0.3% 成本）。
  BT_prem_h20  : premium>=0 且 bt_ratio>=0.5%，持 20 -> /mid
  BT_prem2_h20 : premium>=2% 且 bt_ratio>=0.5%，持 20 -> /mid
  BT_prem_h60  : 同 BT_prem_h20，持 60 -> /long
排序分 = bt_ratio 降序，平手按代码升序。liq，k_slots=20, max_new=5。cohort_bench 同日同市值五分位对照。
参照（算重叠）：EA_h20、F_any_h20、RP_r05、INC_opt_h20、SCW（/mid），RP_big_h60（/long long-repurchase-big）。
用法：LT_START=.. LT_END=.. python lt_research25.py "<phase>,<phase>" out.json"""
import sys, json, os, glob
from pathlib import Path
import polars as pl
from lt_research import fixed, PH
from realistic import run_real, metrics_real
from lt_research6_ctrl import cohort_bench
import lt_research20 as R20
import lt_research23 as R23
c = pl.col
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
BT = Path(__file__).resolve().parents[2] / "output/research/sprint4/block_trade"
RATIO_TH = 0.005


def load_block(start=None, end=None) -> pl.DataFrame:
    """(code, trade_date YYYY-MM-DD, bt_amt 万元, bt_vol 万股, bt_n) 当日汇总。"""
    fs = sorted(glob.glob(str(BT / "*.parquet")))
    if start: fs = [f for f in fs if Path(f).stem >= start.replace("-", "")]
    if end: fs = [f for f in fs if Path(f).stem <= end.replace("-", "")]
    parts = []
    for f in fs:
        x = pl.read_parquet(f)
        if x.height == 0: continue
        parts.append(x.select(c("ts_code").cast(pl.Utf8), c("trade_date").cast(pl.Utf8),
                              c("vol").cast(pl.Float64, strict=False), c("amount").cast(pl.Float64, strict=False)))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["vol", "amount"]).filter((c("vol") > 0) & (c("amount") > 0))
    d = d.with_columns(c("ts_code").str.slice(0, 6).alias("code"),
                       pl.concat_str([c("trade_date").str.slice(0, 4), c("trade_date").str.slice(4, 2), c("trade_date").str.slice(6, 2)],
                                     separator="-").alias("trade_date"))
    d = d.filter(c("code").str.contains(r"^\d{6}$") & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
                 & ~c("ts_code").str.ends_with(".BJ"))
    return d.group_by("code", "trade_date").agg(c("amount").sum().alias("bt_amt"), c("vol").sum().alias("bt_vol"),
                                                pl.len().alias("bt_n"))


def bt_features(df: pl.DataFrame) -> pl.DataFrame:
    s = df["trade_date"].min()
    b = load_block(s)
    px = df.select("code", c("trade_date").alias("bd"), c("close").alias("close_bd"))
    df = df.join(px, on=["code", "bd"], how="left")
    fmv = c("circ_mv").cast(pl.Float64, strict=False) * c("close") / c("close_bd")
    df = df.join(b, on=["code", "trade_date"], how="left").with_columns(fmv.alias("fmv"))
    return df.with_columns(((c("bt_amt") / c("bt_vol")) / c("close") - 1).alias("bt_prem"),
                           (c("bt_amt") / c("fmv")).alias("bt_ratio")).drop("close_bd")


def prep():
    df = R23.prep()
    df = bt_features(df)
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    ev = c("liq") & c("bt_amt").is_not_null() & (c("bt_ratio") >= RATIO_TH)
    return df.with_columns(
        (ev & (c("bt_prem") >= 0)).fill_null(False).alias("BTP"),
        (ev & (c("bt_prem") >= 0.02)).fill_null(False).alias("BTP2"),
        (c("bt_ratio").fill_null(0) - tie).alias("sc_bt"),
        (c("liq") & c("rp").fill_null(False) & (c("sc_r") >= 0.01)).fill_null(False).alias("RP_big"),
    ).sort("code", "trade_date")


C = {"BT_prem_h20": ("BTP", "sc_bt", 20, 5), "BT_prem2_h20": ("BTP2", "sc_bt", 20, 5), "BT_prem_h60": ("BTP", "sc_bt", 60, 5),
     "EA_h20": ("G", "sc_g", 20, 5), "F_any_h20": ("FA", "sc_a", 20, 5), "RP_r05": ("RP_r05", "sc_r", 20, 5),
     "INC_opt_h20": ("IO", "sc_i", 20, 5), "SCW": ("SCW", "sc_small", 20, 10), "RP_big_h60": ("RP_big", "sc_r", 60, 5)}
REFS = ("EA_h20", "F_any_h20", "RP_r05", "INC_opt_h20", "SCW", "RP_big_h60")
VARS = ("BT_prem_h20", "BT_prem2_h20", "BT_prem_h60")

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("block-trade rows (liq, code-day) per year:", df.filter(c("liq") & c("bt_amt").is_not_null()).group_by(yr)
          .agg(pl.len().alias("any"), (c("bt_prem") >= 0).sum().alias("prem0"), (c("bt_ratio") >= RATIO_TH).sum().alias("r05"),
               c("BTP").sum(), c("BTP2").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        trades = {}
        for n, (sig, sc, h, mn) in C.items():
            t, cur = run_real(df, sig, sc, fixed(h), s, e, k_slots=20, max_new=mn, max_hold=h)
            m = metrics_real(t, cur)
            if t is not None and t.height:
                b = cohort_bench(df, t)
                m["bench"] = round(100 * float(b["bench"].mean()), 2); m["excess"] = round(m["avg"] - m["bench"], 2)
                m["ex_win"] = round(100 * float(((b["ret"] - b["bench"]) > 0).mean()), 1)
                r = t["ret"].to_numpy()
                m["avg_win"] = round(100 * float(r[r > 0].mean()), 2) if (r > 0).any() else 0.0
                m["avg_loss"] = round(100 * float(r[r <= 0].mean()), 2) if (r <= 0).any() else 0.0
                streak = mx = 0
                for x in t.sort("entry")["ret"].to_list():
                    streak = streak + 1 if x <= 0 else 0; mx = max(mx, streak)
                m["max_loss_streak"] = mx
            trades[n] = t
            res.setdefault(n, {})[ph] = {a: float(v) for a, v in m.items()}
            print(n, ph, m, flush=True)
        for n in VARS:
            for rname in REFS:
                res[n][ph][f"overlap_{rname}"] = R20.overlap(trades[n], trades[rname])
            print("  overlap", n, {r: res[n][ph][f"overlap_{r}"]["frac_hold_overlap"] for r in REFS}, flush=True)
        if ph in ("R12 2025-10~2026-10", "N3 2024-01~2026-10"):
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            for n, t in trades.items():
                if n in VARS and t is not None and t.height:
                    t.write_csv(out.replace(".json", f"_{n}_{ph.split()[0]}_trades.csv"))
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
