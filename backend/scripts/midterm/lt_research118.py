"""[中线] §312「小盘高股息」SDY_W_h20（邢不行小市值周度框架 + 红利：
在盈利、流动的最小 400 只小票（rk_s≤400，小盘指数成分口径）里选股息率TTM最高 10 只）— 预注册（docs/strategy-research.md §312，结果出来前写定，不调参）。
  复用 lt_research104.prep()（mvd 折算市值、小盘等权指数闸门 scid>scid20、SCW 参照列）；ST 口径 LPS_ST_SRC=db（stock_metadata_history.is_st）。
  Uy = rk_s<=400（amt20>3000万 & age>250 & 非ST & 非北交所 & mvd 非空 & pe_ttm>0 内市值升序前400）& dv_ttm>0；rk_y = Uy 内 dv_ttm 降序名次。
  SDY_W：周五 & 小盘等权指数>MA20 & rk_y<=10；次日开盘买、持 20 日。K=20、max_new=10（仅回测，实盘不设仓位上限）。
  同进程跑现役 SCW 作参照（不是候选），报同周重叠率；若 N3 平均重叠 ≥50%，需 N3 每笔高于同进程 SCW 才算通过。
用法：LPS_ST_SRC=db LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research118.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research104 import prep as prep104
c = pl.col
NAME = "SDY_W_h20"


def prep():
    df = prep104()
    wk = (c("trade_date").str.to_date().dt.weekday() == 5)
    Uy = ((c("rk_s") <= 400) & (c("dv_ttm") > 0)).fill_null(False)
    df = df.with_columns(pl.when(Uy).then(-c("dv_ttm")).rank("ordinal").over("trade_date").alias("rk_y"),
                         c("dv_ttm").fill_null(-1).alias("sc_dy"))
    on = c("scid") > c("scid20")
    return df.with_columns((wk & on & (c("rk_y") <= 10)).fill_null(False).alias("SDY_W")).sort("code", "trade_date")


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
        o = w.filter(c("SDY_W")).group_by("trade_date").agg(pl.len().alias("a"), c("SCW").sum().alias("b"))
        info = {"sigdays": o.height}
        if o.height:
            sel = w.filter(c("SDY_W"))
            info.update({"avg_n": round(float(o["a"].mean()), 2), "ovl_SCW": round(float((o["b"] / o["a"]).mean()), 3),
                         "dv_med": round(float(sel["dv_ttm"].median()), 2), "pe_med": round(float(sel["pe_ttm"].median()), 1),
                         "rk_s_med": round(float(sel["rk_s"].median()), 0), "mvd_med_yi": round(float(sel["mvd"].median()) / 1e4, 1), "close_med": round(float(sel["close"].median()), 2),
                         "r20_med": round(float(sel["r20"].median()), 3), "n_codes": sel["code"].n_unique()})
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        del w
        for n, sig, sc in ((NAME, "SDY_W", "sc_dy"), ("SCW_ref", "SCW", "sc_small")):
            t, cur = run_real(df, sig, sc, fixed(20), s, e, k_slots=20, max_new=10, max_hold=20)
            m = full_metrics(df, t, cur)
            if t is not None and t.height:
                m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
                if n == NAME:
                    t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
            res.setdefault(n, {})[ph] = m
            print(n, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
