"""[短线] §330「小市值每周换仓·持 5 日」SCW5_h5 / §331「长假前小市值（节前效应）」HOL5_h5 — 预注册（docs/strategy-research.md §330/§331，结果出来前写定，不调参）。
  复用 lt_research104.prep()（mvd 折算市值、最小 400 盈利小票等权指数闸门 scid>scid20、rk_s、SCW 现役列）；ST 口径 LPS_ST_SRC=db。
  SCW5_h5：与现役 mid-small-cap-weekly 同一选股（周五 & scid>scid20 & rk_s<=10），只把持有期从 20 日改成 5 日（次日开盘买，第 5 个持有日后的开盘卖）。
  HOL5_h5：长假 = 本交易日到下一交易日相隔 ≥4 个自然日（比普通周末多休至少 1 天）；L = 长假前最后一个交易日；
           信号日 S = L 往前数第 4 个交易日（S 收盘出信号，L−3 开盘买，持 5 日 → 节后第 2 个交易日开盘卖）；
           S 当日 scid>scid20 & rk_s<=10，按市值升序。
  引擎 realistic：开盘涨停买不进不递补、跌停锁死顺延、0.3% 成本；K=20、max_new=10（仅回测，实盘不设仓位上限）。
  同进程参照（非候选）：SCW_ref = 现役 SCW 持 20 日。
用法：LPS_ST_SRC=db LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research126.py "<phase>" out.json"""
import sys, json, os, datetime as dt
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research104 import prep as prep104
c = pl.col
RUNS = (("SCW5_h5", "SCW", 5), ("HOL5_h5", "HOL5", 5), ("SCW_ref", "SCW", 20))
SAVE = ("SCW5_h5", "HOL5_h5")


def holiday_days(dates):
    """返回 (长假前最后交易日集合 L, 信号日集合 S)。dates 为升序 YYYY-MM-DD。"""
    ds = [dt.date.fromisoformat(x) for x in dates]
    L, S = [], []
    for i in range(len(ds) - 1):
        if (ds[i + 1] - ds[i]).days >= 4:
            L.append(dates[i])
            if i - 4 >= 0:
                S.append(dates[i - 4])
    return L, S


def prep():
    df = prep104()
    dates = df.select("trade_date").unique().sort("trade_date")["trade_date"].to_list()
    L, S = holiday_days(dates)
    on = c("scid") > c("scid20")
    df = df.with_columns(c("trade_date").is_in(S).alias("hol_s"))
    df = df.with_columns((c("hol_s") & on & (c("rk_s") <= 10)).fill_null(False).alias("HOL5"))
    return df.sort("code", "trade_date"), L, S


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0; mx = max(mx, k)
    return mx


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df, L, S = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        w = df.filter(c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        s_in = [x for x in S if s <= x <= e]
        gate = (w.filter(c("trade_date").is_in(s_in)).group_by("trade_date")
                .agg((c("scid") > c("scid20")).first().alias("on")).sort("trade_date"))
        scw_days = w.filter(c("SCW"))["trade_date"].n_unique()
        sel = w.filter(c("SCW"))
        info = {"holiday_L": [x for x in L if s <= x <= e], "hol_signal_days": len(s_in),
                "hol_gate_open": int(gate["on"].sum()) if gate.height else 0,
                "scw_sigdays": scw_days,
                "scw_mvd_med_yi": round(float(sel["mvd"].median()) / 1e4, 1) if sel.height else None,
                "scw_n_codes": sel["code"].n_unique()}
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        del w, sel
        for n, sig, h in RUNS:
            t, cur = run_real(df, sig, "sc_small", fixed(h), s, e, k_slots=20, max_new=10, max_hold=h)
            m = full_metrics(df, t, cur)
            if t is not None and t.height:
                m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
                m["per_day"] = round(m["avg"] / max(m["days"], 1e-9), 3)
                if n in SAVE:
                    t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{n}_{ph.split()[0]}.csv"))
            res.setdefault(n, {})[ph] = m
            print(n, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
