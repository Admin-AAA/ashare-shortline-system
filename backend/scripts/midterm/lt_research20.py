"""[中线] §103 业绩预告正向漂移（首次预告）— 预注册 3 版（/workspace/strategy-research.md §103，结果出来前写定，不调参）。
数据：data/midterm/forecast/<code>.parquet（tushare forecast）。同一 (code, end_date) 只取最早一次公告（修正公告不算新信号）。
PIT：ann_date -> >= ann_date 的第一个交易日 sd，sd 收盘出信号，次日开盘买（run_real：开盘涨停不买、跌停锁死顺延、0.3% 成本）。
同一 (code, sd) 多个报告期：任一合格即合格，分数取最大。排序分 = min(p_change_min, 300) 降序，平手按代码升序。
  F_big_h20 : type in {预增, 扭亏} 且 p_change_min >= 50 且 net_profit_min > 0，持 20 日 -> /mid
  F_any_h20 : type in {预增, 扭亏, 略增} 且 p_change_min > 0 且 net_profit_min > 0，持 20 日 -> /mid
  F_big_h60 : 同 F_big，持 60 日 -> /long
通用：liq（20日均额>5000万、上市>250日、非ST、非北交所），k_slots=20, max_new=5。
对照：cohort_bench（同入场/出场日、信号日同市值五分位流动性合格全体等权），每笔超额 = 策略 − 基准。
另跑现行 EA_h20（lt_research15 / §93 G 条件，持20）报告重叠：同 code 同入场日、同 code 持有期有交集。
用法：LT_START=.. LT_END=.. python lt_research20.py "<phase>,<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import build, fixed, PH
from realistic import run_real, metrics_real
from lt_research6_ctrl import cohort_bench
from lt_research12 import _pit
from lt_research14 import fina_sig
import lt_data as L
c = pl.col
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
COLS = ["ts_code", "ann_date", "end_date", "type", "p_change_min", "net_profit_min"]


def forecast_first():
    parts = []
    for f in sorted(glob.glob(str(L.D / "forecast" / "*.parquet"))):
        try:
            x = pl.read_parquet(f)
        except Exception:
            continue
        if not set(COLS) <= set(x.columns) or x.height == 0: continue
        parts.append(x.select([c(k).cast(pl.Utf8) if k in ("ts_code", "ann_date", "end_date", "type")
                               else c(k).cast(pl.Float64, strict=False) for k in COLS]))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date"])
    d = d.with_columns(c("ann_date").str.strip_chars()).filter(c("ann_date").str.len_chars() == 8)
    return d.sort("ann_date").unique(["ts_code", "end_date"], keep="first", maintain_order=True)


def fc_sig(td):
    d = forecast_first()
    big = c("type").is_in(["预增", "扭亏"]) & (c("p_change_min") >= 50) & (c("net_profit_min") > 0)
    anyp = c("type").is_in(["预增", "扭亏", "略增"]) & (c("p_change_min") > 0) & (c("net_profit_min") > 0)
    d = d.with_columns(big.fill_null(False).alias("fb"), anyp.fill_null(False).alias("fa"),
                       c("p_change_min").clip(-1e9, 300).alias("fsc"))
    d = d.filter(c("fa"))  # fb ⊂ fa
    d = _pit(d, td)
    return (d.group_by("code", "sd").agg(c("fb").any().alias("fb"), c("fa").any().alias("fa"),
                                         pl.when(c("fb")).then(c("fsc")).max().alias("sc_b"), c("fsc").max().alias("sc_a"))
             .rename({"sd": "trade_date"}))


def prep():
    df = build()
    td = df.select("trade_date").unique().sort("trade_date")
    df = df.join(fc_sig(td), on=["code", "trade_date"], how="left").join(fina_sig(td), on=["code", "trade_date"], how="left")
    G = c("liq") & (c("pdd") > 0) & (c("dtp") >= 50) & (c("orv") >= 20) & (c("dtp") > c("prev_dtp"))
    return df.with_columns(
        (c("liq") & c("fb").fill_null(False)).fill_null(False).alias("FB"),
        (c("liq") & c("fa").fill_null(False)).fill_null(False).alias("FA"),
        # 平手按代码升序（实盘 sorted(-score, code) 同序）：减去 code*1e-10（最大 1e-4）
        (c("sc_b").fill_null(0) - c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10).alias("sc_b"),
        (c("sc_a").fill_null(0) - c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10).alias("sc_a"),
        G.fill_null(False).alias("G"), c("dtp").clip(-1e9, 300).fill_null(0).alias("sc_g")).sort("code", "trade_date")


C = {"F_big_h20": ("FB", "sc_b", 20), "F_any_h20": ("FA", "sc_a", 20), "F_big_h60": ("FB", "sc_b", 60),
     "EA_h20": ("G", "sc_g", 20)}


def overlap(t, e):
    if t is None or t.height == 0 or e is None or e.height == 0:
        return {"same_entry": 0, "hold_overlap": 0, "frac_hold_overlap": 0.0}
    same = t.join(e.select("code", "entry"), on=["code", "entry"], how="semi").height
    tt = t.with_row_index("tid")
    j = tt.join(e.select("code", c("entry").alias("e0"), c("exit").alias("e1")), on="code")
    ov = j.filter((c("entry") < c("e1")) & (c("e0") < c("exit")))["tid"].n_unique()
    return {"same_entry": same, "hold_overlap": int(ov), "frac_hold_overlap": round(ov / t.height, 3),
            "frac_same_entry": round(same / t.height, 3)}


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    print("signal days per year:", df.filter(c("FA")).group_by(c("trade_date").str.slice(0, 4).alias("y"))
          .agg(c("FB").sum(), c("FA").sum(), c("G").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        trades = {}
        for n, (sig, sc, h) in C.items():
            t, cur = run_real(df, sig, sc, fixed(h), s, e, k_slots=20, max_new=5, max_hold=h)
            m = metrics_real(t, cur)
            if t is not None and t.height:
                b = cohort_bench(df, t)
                m["bench"] = round(100 * float(b["bench"].mean()), 2); m["excess"] = round(m["avg"] - m["bench"], 2)
                m["ex_win"] = round(100 * float(((b["ret"] - b["bench"]) > 0).mean()), 1)
                r = t["ret"].to_numpy()
                m["avg_win"] = round(100 * float(r[r > 0].mean()), 2) if (r > 0).any() else 0.0
                m["avg_loss"] = round(100 * float(r[r <= 0].mean()), 2) if (r <= 0).any() else 0.0
                # 最长连亏（按入场日排序）
                streak = mx = 0
                for x in t.sort("entry")["ret"].to_list():
                    streak = streak + 1 if x <= 0 else 0; mx = max(mx, streak)
                m["max_loss_streak"] = mx
            trades[n] = t
            res.setdefault(n, {})[ph] = {a: float(v) for a, v in m.items()}
            print(n, ph, m, flush=True)
        for n in ("F_big_h20", "F_any_h20", "F_big_h60"):
            res[n][ph]["overlap_EA_h20"] = overlap(trades[n], trades["EA_h20"])
            print("  overlap", n, res[n][ph]["overlap_EA_h20"], flush=True)
        if ph in ("R12 2025-10~2026-10", "N3 2024-01~2026-10"):
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            for n, t in trades.items():
                if t is not None and t.height:
                    t.write_csv(out.replace(".json", f"_{n}_{ph.split()[0]}_trades.csv"))
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
