"""[短线研究 §336]「股东减持公告后次日急跌、未跌停 → 再次日开盘买、持 5 日」DSR_h5（减持公告的过度反应反转：
减持是已知、有上限的供给冲击，公告后第一天恐慌抛售常过头）。预注册 docs/strategy-research.md §336，结果出来前写定，不调参。
  数据：data/midterm/events/holdertrade_de_*.parquet（tushare stk_holdertrade 减持，公告日 PIT）。
  事件：按 (code, ann_date) 汇总 change_ratio（占总股本 %，所有股东类型 C/G/P）；合计 ≥0.5% 才算（剔除零碎减持）。
        反应日 E = 第一个 > ann_date 的交易日（公告多在盘后/盘前，E 日价格已完整反映）；同股 20 个自然日内只认第一次公告。
  条件：E 日复权涨跌 ≤ −5% 且 E 日收盘 > 跌停价+0.005（没封死跌停，可成交）& E 日 liq（amt20>5000万 & 上市>250日 & 非ST & 非北交所）。
        同日按 E 日跌幅（pct_chg 升序，跌得最多优先）取前 5。
  引擎 realistic：E+1 开盘买（开盘涨停买不进、不递补）、持 5 日、跌停锁死顺延、0.3% 成本；K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/dsr_s336.py "<phase>" out.json"""
import sys, json, os, glob
from datetime import timedelta
import polars as pl
import lt_data as L
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
HOLD, K, MAX_NEW, NAME, CR_MIN, DROP, DEDUP_D = 5, 20, 5, "DSR_h5", 0.5, -5.0, 20


def de_events():
    h = pl.concat([pl.read_parquet(f, columns=["ts_code", "ann_date", "change_ratio"])
                   for f in sorted(glob.glob(str(L.D / "events" / "holdertrade_de_*.parquet")))], how="diagonal_relaxed")
    h = h.filter(c("ann_date").str.len_chars() == 8).with_columns(
        c("ts_code").str.slice(0, 6).alias("code"), c("ann_date").str.to_date("%Y%m%d").alias("a"),
        c("change_ratio").cast(pl.Float64, strict=False).fill_null(0)).unique()
    g = h.group_by("code", "a").agg(c("change_ratio").sum().alias("cr")).sort("code", "a")
    rows = []
    for (code,), x in g.partition_by("code", as_dict=True).items():
        last = None
        for a, cr in zip(x["a"].to_list(), x["cr"].to_list()):
            if last is not None and (a - last).days <= DEDUP_D:
                continue
            last = a
            if cr >= CR_MIN:
                rows.append((code, a, float(cr)))
    return pl.DataFrame(rows, schema=["code", "a", "cr"], orient="row")


def prep():
    df = build().sort("code", "trade_date")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    pc = c("close").shift(1).over("code")
    dl = pl.coalesce(c("down_limit"), pc * (1 - band))
    df = df.with_columns((c("close") <= dl + 0.005).fill_null(False).alias("ldc"))
    ev = de_events().with_columns((c("a") + timedelta(days=1)).alias("a1"))
    td = df.select("trade_date").unique().with_columns(c("trade_date").str.to_date().alias("sdd")).sort("sdd")
    ev = ev.sort("a1").join_asof(td, left_on="a1", right_on="sdd", strategy="forward").drop_nulls("trade_date")
    ev = ev.group_by("code", "trade_date").agg(c("cr").max())
    df = df.join(ev, on=["code", "trade_date"], how="left")
    evm = c("cr").is_not_null()
    cand = (evm & c("liq") & (c("pct_chg") <= DROP) & ~c("ldc")).fill_null(False)
    df = df.with_columns(evm.alias("ev"), cand.alias("cand"))
    df = df.with_columns(pl.when(c("cand")).then(c("pct_chg")).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns((c("cand") & (c("_rk") <= MAX_NEW)).fill_null(False).alias("DSR"),
                           (-c("pct_chg")).fill_null(-1e9).alias("sc_dsr")).drop("_rk").sort("code", "trade_date")


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
        w = df.filter(c("DSR") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        a = df.filter(c("ev") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"n_sig": w.height, "n_ev_all": a.height}
        if w.height:
            info.update({"sigdays": w["trade_date"].n_unique(), "n_codes": w["code"].n_unique(),
                         "sigday_pct_med": round(float(w["pct_chg"].median()), 2), "cr_med": round(float(w["cr"].median()), 2),
                         "r20_med": round(float(w["r20"].median()), 3),
                         "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64, strict=False).median()) / 1e4, 1),
                         "month_top": w.group_by(c("trade_date").str.slice(0, 7)).len().sort("len", descending=True).head(3).rows()})
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "DSR", "sc_dsr", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
