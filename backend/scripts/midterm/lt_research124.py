"""[中线] §324「ST 摘帽后买入」STR_h20（撤销风险警示：ST/*ST → 正常简称；涨跌幅限制由 5% 恢复到 10%/20%，
重新进入大量机构/指数/两融的可投资池，被动与主动资金回流）— 预注册（docs/strategy-research.md §324，结果出来前写定，不调参）。
  事件：data/midterm/st_hist.parquet（= stock_metadata_history 的 namechange 区间，与 tushare namechange「撤销ST/撤销*ST」2015–2024 逐条日期一致）
        中 st 由 True 变 False 的区间起点；信号日 d = 该股在区间起点当天或之后的第一个交易日（build() 里 st 从 True 翻成 False 的那一行）。
        只认完全摘帽（*ST→ST 的"摘星"仍是 ST，不算）。
  条件：d 当日 liq（amt20>5000万 & 上市>250日 & 非ST & 非北交所）。排序分 = 总市值升序（同日多只时小的优先；实际同日很少多于 1 只）。
  引擎 realistic：d 收盘出信号、次日开盘买（开盘涨停买不进、不递补）、持 20 日、跌停锁死顺延、0.3% 成本；K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research124.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
NAME, HOLD = "STR_h20", 20


def prep():
    df = build().sort("code", "trade_date")
    ev = (c("st").shift(1).over("code") & ~c("st")).fill_null(False)
    mv = c("total_mv").cast(pl.Float64, strict=False)
    return df.with_columns(ev.alias("ev"), (ev & c("liq")).alias("STR"), (-mv).fill_null(-1e18).alias("sc_str")).sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0; mx = max(mx, k)
    return mx


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        w = df.filter(c("STR") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        a = df.filter(c("ev") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"n_sig": w.height, "n_ev_all": a.height}
        if w.height:
            info.update({"sigdays": w["trade_date"].n_unique(), "sigday_pct_med": round(float(w["pct_chg"].median()), 2),
                         "sigday_lu_share": round(float((w["pct_chg"] >= 9.5).mean()), 3),
                         "r20_med": round(float(w["r20"].median()), 3), "r60_med": round(float(w["r60"].median()), 3),
                         "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64, strict=False).median()) / 1e4, 1),
                         "month_top": w.group_by(c("trade_date").str.slice(5, 2)).len().sort("len", descending=True).head(3).rows()})
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "STR", "sc_str", fixed(HOLD), s, e, k_slots=20, max_new=5, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
