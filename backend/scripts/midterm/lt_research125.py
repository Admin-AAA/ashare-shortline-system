"""[中线] §329「高管/个人集体增持」CIB_h20（cluster insider buying，Cohen–Malloy–Pomorski 2012 / Alldredge–Blank 2019：
多名内部人在短时间内各自用自己的钱买入，比单一股东增持更可信）— 预注册（docs/strategy-research.md §329，结果出来前写定，不调参）。
  数据：data/midterm/events/holdertrade_in_*.parquet（tushare stk_holdertrade 增持，公告日 PIT，2017-07 起）。
  只认 holder_type ∈ {G 高管, P 个人}（剔除 C 公司股东），且 holder_name 不含「计划/信托/资管/基金/合伙/有限」（剔除员工持股计划等产品）。
  事件：对每只股票、每个公告日 a，回看 (a−30 自然日, a] 内不同 holder_name 的增持人数 nb；nb ≥ 3 即触发。
        同股 180 自然日内只认第一次触发。信号日 sd = 第一个 ≥ a 的交易日（公告可能在收盘后），次日开盘买。
  条件：sd 当日 liq（amt20>5000万 & 上市>250日 & 非ST & 非北交所）。排序分 = nb 降序，再按窗口内 change_ratio 合计降序。
  引擎 realistic：次日开盘买（开盘涨停买不进、不递补）、持 20 日、跌停锁死顺延、0.3% 成本；K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research125.py "<phase>" out.json"""
import sys, json, os, glob
from datetime import timedelta
import polars as pl
import lt_data as L
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
NAME, HOLD, NB_MIN, WIN_D, DEDUP_D = "CIB_h20", 20, 3, 30, 180
BAD = "计划|信托|资管|基金|合伙|有限"


def cib_events():
    h = pl.concat([pl.read_parquet(f, columns=["ts_code", "ann_date", "holder_name", "holder_type", "change_ratio"])
                   for f in sorted(glob.glob(str(L.D / "events" / "holdertrade_in_*.parquet")))], how="diagonal_relaxed")
    h = h.filter(c("holder_type").is_in(["G", "P"]) & ~c("holder_name").str.contains(BAD) & (c("ann_date").str.len_chars() == 8))
    h = h.with_columns(c("ts_code").str.slice(0, 6).alias("code"), c("ann_date").str.to_date("%Y%m%d").alias("a"),
                       c("change_ratio").cast(pl.Float64, strict=False).fill_null(0)).unique(["code", "a", "holder_name"])
    rows = []
    for (code,), g in h.sort("a").partition_by("code", as_dict=True).items():
        recs = list(zip(g["a"].to_list(), g["holder_name"].to_list(), g["change_ratio"].to_list()))
        last = None
        for a in sorted(set(r[0] for r in recs)):
            win = [r for r in recs if a - timedelta(days=WIN_D) < r[0] <= a]
            nb = len(set(r[1] for r in win))
            if nb >= NB_MIN and (last is None or (a - last).days > DEDUP_D):
                rows.append((code, a, nb, float(sum(r[2] for r in win)))); last = a
    return pl.DataFrame(rows, schema=["code", "a", "nb", "cr"], orient="row")


def prep():
    df = build().sort("code", "trade_date")
    ev = cib_events()
    td = df.select("trade_date").unique().with_columns(c("trade_date").str.to_date().alias("sdd")).sort("sdd")
    ev = ev.sort("a").join_asof(td, left_on="a", right_on="sdd", strategy="forward").drop_nulls("trade_date")
    ev = ev.group_by("code", "trade_date").agg(c("nb").max(), c("cr").max())
    df = df.join(ev, on=["code", "trade_date"], how="left")
    sig = (c("nb").is_not_null() & c("liq")).fill_null(False)
    return df.with_columns(c("nb").is_not_null().alias("ev"), sig.alias("CIB"),
                           (c("nb").fill_null(0) * 1000 + c("cr").fill_null(0).clip(0, 999)).alias("sc_cib")).sort("code", "trade_date")


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
        w = df.filter(c("CIB") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        a = df.filter(c("ev") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"n_sig": w.height, "n_ev_all": a.height}
        if w.height:
            info.update({"sigdays": w["trade_date"].n_unique(), "nb_med": float(w["nb"].median()),
                         "sigday_pct_med": round(float(w["pct_chg"].median()), 2),
                         "r20_med": round(float(w["r20"].median()), 3), "r60_med": round(float(w["r60"].median()), 3),
                         "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64, strict=False).median()) / 1e4, 1),
                         "month_top": w.group_by(c("trade_date").str.slice(0, 7)).len().sort("len", descending=True).head(3).rows()})
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "CIB", "sc_cib", fixed(HOLD), s, e, k_slots=20, max_new=5, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
