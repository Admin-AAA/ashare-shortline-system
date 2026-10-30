"""[中线] §348「预告预喜」两版过滤 F_any_nr_h20 / F_any_noz_h20 — 预注册（docs/strategy-research.md §348，结果出来前写定，不调参）。
  原策略 F_any_h20（现役 mid 预告预喜，lt_research20 口径）：sd 日 liq & F_any 事件，按 min(p_change_min,300) 降序，次日开盘买、持 20 日；K=20、max_new=5。
  F_any_nr_h20：同上，但候选要求 r20≤0.20（剔除公告前 20 日已大涨），同日其余候选按原排序补上。
  F_any_noz_h20：同上，但 type 只留 {预增, 略增}（剔除扭亏），分数同 sc_a 口径。
用法：LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research134.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research20 import prep as prep20, forecast_first
from lt_research12 import _pit
c = pl.col
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
RUNS = (("F_any_h20", "FA", "sc_a"), ("F_any_nr_h20", "FA_nr", "sc_a"), ("F_any_noz_h20", "FA_noz", "sc_noz"))


def prep():
    df = prep20()
    td = df.select("trade_date").unique().sort("trade_date")
    d = forecast_first()
    noz = c("type").is_in(["预增", "略增"]) & (c("p_change_min") > 0) & (c("net_profit_min") > 0)
    d = d.with_columns(noz.fill_null(False).alias("fn"), c("p_change_min").clip(-1e9, 300).alias("fsc"))
    d = d.filter(c("fn"))
    d = _pit(d, td)
    sig = (d.group_by("code", "sd").agg(c("fn").any().alias("fn"), c("fsc").max().alias("sc_noz_raw"))
             .rename({"sd": "trade_date"}))
    df = df.join(sig, on=["code", "trade_date"], how="left")
    return df.with_columns(
        (c("FA") & (c("r20").fill_null(9.0) <= 0.20)).fill_null(False).alias("FA_nr"),
        (c("liq") & c("fn").fill_null(False)).fill_null(False).alias("FA_noz"),
        (c("sc_noz_raw").fill_null(0) - c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10).alias("sc_noz"),
    ).sort("code", "trade_date")


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
        info = {
            "FA_events": int(w["FA"].sum()),
            "FA_nr_events": int(w["FA_nr"].sum()),
            "FA_noz_events": int(w["FA_noz"].sum()),
            "FA_r20_gt20_events": int((w["FA"] & (w["r20"].fill_null(0) > 0.20)).sum()),
        }
        res.setdefault("_info", {})[ph] = info
        print("INFO", ph, info, flush=True)
        del w
        for n, sig, sc in RUNS:
            t, cur = run_real(df, sig, sc, fixed(20), s, e, k_slots=20, max_new=5, max_hold=20)
            m = full_metrics(df, t, cur)
            if t is not None and t.height:
                m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
                m["per_day"] = round(m["avg"] / max(m["days"], 1e-9), 3)
                t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{n}_{ph.split()[0]}.csv"))
            res.setdefault(n, {})[ph] = m
            print(n, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
