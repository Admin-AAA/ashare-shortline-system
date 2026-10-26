"""[中线] §334「预告预喜 + 首日收跌否决」F_any_nd_h20 — 预注册（docs/strategy-research.md §334，结果出来前写定，不调参）。
  原策略 F_any_h20（现役 mid 预告预喜，lt_research20 口径）：sd 日 liq & F_any 事件，按 min(p_change_min,300) 降序，次日开盘买、持 20 日；K=20、max_new=5。
  F_any_nd_h20：同上，但候选里去掉 sd 当日 pct_chg <0 的票（§333「好消息却跌」），同日其余候选按原排序补上（与实盘在候选层过滤的口径一致）。
  另记被否成交：原策略成交里 sd 当日收跌的那部分（只作说明）。
用法：LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research127.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research20 import prep as prep20
c = pl.col
RUNS = (("F_any_h20", "FA"), ("F_any_nd_h20", "FA_nd"))


def prep():
    df = prep20()
    return df.with_columns((c("FA") & (c("pct_chg") >= 0)).fill_null(False).alias("FA_nd")).sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0; mx = max(mx, k)
    return mx


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    dn = df.filter(c("FA") & (c("pct_chg") < 0)).select("code", "trade_date")
    for ph in phases:
        s, e = PH[ph]
        w = df.filter(c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"FA_events": int(w["FA"].sum()), "FA_down_events": int((w["FA"] & (w["pct_chg"] < 0)).sum())}
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        del w
        trades = {}
        for n, sig in RUNS:
            t, cur = run_real(df, sig, "sc_a", fixed(20), s, e, k_slots=20, max_new=5, max_hold=20)
            m = full_metrics(df, t, cur)
            if t is not None and t.height:
                m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
                m["per_day"] = round(m["avg"] / max(m["days"], 1e-9), 3)
                t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{n}_{ph.split()[0]}.csv"))
            trades[n] = t
            res.setdefault(n, {})[ph] = m
            print(n, ph, m, flush=True)
        t0 = trades["F_any_h20"]
        if t0 is not None and t0.height:
            # 原策略成交的信号日 = 入场日前一个交易日
            td = df.select("trade_date").unique().sort("trade_date").with_columns(c("trade_date").shift(-1).alias("entry"))
            t0s = t0.join(td, on="entry", how="left").rename({"trade_date": "sd"})
            hit = t0s.join(dn.rename({"trade_date": "sd"}), on=["code", "sd"], how="semi")
            res["F_any_h20"][ph]["vetoed"] = {"n": hit.height,
                                              "avg": round(100 * float(hit["ret"].mean()), 2) if hit.height else None,
                                              "share": round(hit.height / t0.height, 3)}
            print("VETOED", ph, res["F_any_h20"][ph]["vetoed"], flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
