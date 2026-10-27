"""[中线] §305「低市净率 × 小市值」LPB_W_h20（邢不行《“垃圾股”策略》思路的估值版：把「股价最低 200」换成「市净率最低 200」，
破净/低 PB 小票 = 便宜 + 小）— 预注册（docs/strategy-research.md §305，结果出来前写定，不调参）。
  复用 lt_research104.prep()（mvd 折算市值、小盘等权指数闸门 scid>scid20、LPS_W/SCW 参照列）；ST 口径用 stock_metadata_history.is_st
  （LPS_ST_SRC=db，与 LPS_W 上线复核同口径）。
  U = amt20>3000万 & age>250 & 非ST & 非北交所 & mvd 非空 & close>2 & pb>0；rb = U 内 pb 升序名次，rm = U 内 mvd 升序名次。
  LPB_W：周五 & 小盘等权指数>MA20，rb<=200 & rm<=200，按 mvd 升序前 10；次日开盘买、持 20 日。
  K=20、max_new=10（仅回测，实盘不设仓位上限）；cohort_bench 同市值五分位对照（参考项）。另报与 LPS_W / SCW 同周选股重叠率。
用法：LPS_ST_SRC=db LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research116.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research104 import prep as prep104
c = pl.col
NAME = "LPB_W_h20"


def prep():
    df = prep104()
    wk = (c("trade_date").str.to_date().dt.weekday() == 5)
    U = ((c("amt20") > 3e7) & (c("age") > 250) & ~c("st") & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
         & c("mvd").is_not_null() & (c("close") > 2) & (c("pb") > 0)).fill_null(False)
    df = df.with_columns(pl.when(U).then(c("pb")).rank("ordinal").over("trade_date").alias("rb"),
                         pl.when(U).then(c("mvd")).rank("ordinal").over("trade_date").alias("rm2"))
    lb = (U & (c("rb") <= 200) & (c("rm2") <= 200)).fill_null(False)
    df = df.with_columns(pl.when(lb).then(c("mvd")).rank("ordinal").over("trade_date").alias("rk_lb"))
    on = c("scid") > c("scid20")
    return df.with_columns((wk & on & (c("rk_lb") <= 10)).fill_null(False).alias("LPB_W")).sort("code", "trade_date")


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
        w = df.filter(c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        o = (w.filter(c("LPB_W")).group_by("trade_date")
             .agg(pl.len().alias("a"), c("LPS_W").sum().alias("b1"), c("SCW").sum().alias("b2")))
        info = {"sigdays": o.height}
        if o.height:
            sel = w.filter(c("LPB_W"))
            info.update({"avg_n": round(float(o["a"].mean()), 2), "ovl_LPS_W": round(float((o["b1"] / o["a"]).mean()), 3),
                         "ovl_SCW": round(float((o["b2"] / o["a"]).mean()), 3), "pb_med": round(float(sel["pb"].median()), 2),
                         "close_med": round(float(sel["close"].median()), 2), "mvd_med_yi": round(float(sel["mvd"].median()) / 1e4, 1),
                         "pe_neg_share": round(float((sel["pe_ttm"].is_null() | (sel["pe_ttm"] <= 0)).mean()), 3),
                         "r20_med": round(float(sel["r20"].median()), 3)})
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        del w
        t, cur = run_real(df, "LPB_W", "sc_small", fixed(20), s, e, k_slots=20, max_new=10, max_hold=20)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
