"""[中线] §317「小盘走弱时切大盘红利」BDG_W_h20（大小盘风格切换：现役小盘周度类（SCW/LPS/SDY/SPS）在小盘指数跌破 MA20 时全部停开新仓，
本条只在这段"闸门关闭"期出手，买大市值高股息票）— 预注册（docs/strategy-research.md §317，结果出来前写定，不调参）。
  复用 lt_research119.prep()（= lt_research104/118 + SPS_W；SCW / SDY_W / SPS_W / LPS_W 现役参照列；ST 口径 LPS_ST_SRC=db）。
  Ub = amt20>3000万 & age>250 & 非ST & 非北交所 & mvd 非空 & pe_ttm>0 & dv_ttm>0；rk_b = Ub 内 mvd 降序名次（最大市值=1）。
  Bp = Ub & rk_b<=300；rk_d = Bp 内 dv_ttm 降序名次。
  BDG_W：周五 & scid <= scid20（小盘等权指数不在 MA20 之上，与 SCW 闸门正好相反）& rk_d<=10；次日开盘买、持 20 日。K=20、max_new=10（仅回测，实盘不设仓位上限）。
  参照（非候选）：BDA_ref = 同选股但不看闸门（每个周五都买），用来看"只在小盘走弱时买"是否有用；SCW_ref 现役。
用法：LPS_ST_SRC=db LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research121.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research119 import prep as prep119
c = pl.col
NAME = "BDG_W_h20"
REFS = (("BDA_ref", "BDA_W", "sc_bd"), ("SCW_ref", "SCW", "sc_small"))


def prep():
    df = prep119()
    wk = (c("trade_date").str.to_date().dt.weekday() == 5)
    Ub = ((c("amt20") > 3e7) & (c("age") > 250) & ~c("st") & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
          & c("mvd").is_not_null() & (c("pe_ttm") > 0) & (c("dv_ttm") > 0)).fill_null(False)
    df = df.with_columns(pl.when(Ub).then(-c("mvd")).rank("ordinal").over("trade_date").alias("rk_b"))
    Bp = (Ub & (c("rk_b") <= 300)).fill_null(False)
    df = df.with_columns(pl.when(Bp).then(-c("dv_ttm")).rank("ordinal").over("trade_date").alias("rk_d"),
                         c("dv_ttm").fill_null(-1).alias("sc_bd"))
    off = (c("scid") <= c("scid20"))
    return df.with_columns((wk & off & (c("rk_d") <= 10)).fill_null(False).alias("BDG_W"),
                           (wk & (c("rk_d") <= 10)).fill_null(False).alias("BDA_W")).sort("code", "trade_date")


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
        fri = w.filter(c("trade_date").str.to_date().dt.weekday() == 5).select("trade_date", (c("scid") <= c("scid20")).alias("off")).unique("trade_date")
        o = w.filter(c("BDG_W")).group_by("trade_date").agg(pl.len().alias("a"), c("SCW").sum().alias("b1"), c("SDY_W").sum().alias("b2"),
                                                            c("SPS_W").sum().alias("b3"), c("LPS_W").sum().alias("b4"))
        info = {"sigdays": o.height, "fridays": fri.height, "off_share": round(float(fri["off"].fill_null(False).mean()), 3) if fri.height else None}
        if o.height:
            sel = w.filter(c("BDG_W"))
            info.update({"avg_n": round(float(o["a"].mean()), 2), "ovl_SCW": round(float((o["b1"] / o["a"]).mean()), 3),
                         "ovl_SDY": round(float((o["b2"] / o["a"]).mean()), 3), "ovl_SPS": round(float((o["b3"] / o["a"]).mean()), 3),
                         "ovl_LPS": round(float((o["b4"] / o["a"]).mean()), 3),
                         "dv_med": round(float(sel["dv_ttm"].median()), 2), "pe_med": round(float(sel["pe_ttm"].median()), 1),
                         "pb_med": round(float(sel["pb"].median()), 2), "mvd_med_yi": round(float(sel["mvd"].median()) / 1e4, 1),
                         "r20_med": round(float(sel["r20"].median()), 3), "n_codes": sel["code"].n_unique()})
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        del w
        for n, sig, sc in ((NAME, "BDG_W", "sc_bd"),) + REFS:
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
