"""[中线] §339「业绩预告预喜 + 公告后首个交易日收跌」F_any_dn_h20 — 预注册（docs/strategy-research.md §339，结果出来前写定，不调参）。
  原策略 F_any_h20（现役 mid 预告预喜，lt_research20 口径）：sd 日 liq & F_any 事件，按 min(p_change_min,300) 降序，次日开盘买、持 20 日；K=20、max_new=5。
  F_any_dn_h20：同上，只保留 sd 当日 pct_chg <0 的候选（同日其余合格候选按原排序补位）。
  F_any_up_h20：反向对照，只保留 sd 当日 pct_chg ≥0（= §334 F_any_nd_h20）。
  每笔写 complete = days ≥20（满期卖出）；数据末端被截断的笔 complete=False，评估时单列。
用法：LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research129.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research20 import prep as prep20
c = pl.col
PH = dict(PH); PH["OOS 2019-01~2025-09"] = ("2019-01-01", "2025-09-30")
RUNS = (("F_any_h20", "FA"), ("F_any_dn_h20", "FA_dn"), ("F_any_up_h20", "FA_up"))
HOLD = 20


def prep():
    df = prep20()
    return df.with_columns((c("FA") & (c("pct_chg") < 0)).fill_null(False).alias("FA_dn"),
                           (c("FA") & (c("pct_chg") >= 0)).fill_null(False).alias("FA_up")).sort("code", "trade_date")


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        w = df.filter(c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"FA_events": int(w["FA"].sum()), "FA_dn_events": int(w["FA_dn"].sum()), "FA_up_events": int(w["FA_up"].sum()),
                "last_data_date": df["trade_date"].max()}
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        del w
        for n, sig in RUNS:
            t, cur = run_real(df, sig, "sc_a", fixed(HOLD), s, e, k_slots=20, max_new=5, max_hold=HOLD)
            m = full_metrics(df, t, cur)
            if t is not None and t.height:
                t = t.with_columns((c("days") >= HOLD).alias("complete"))
                m["n_unfinished"] = int((~t["complete"]).sum())
                t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{n}_{ph.split()[0]}.csv"))
            res.setdefault(n, {})[ph] = m
            print(n, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
