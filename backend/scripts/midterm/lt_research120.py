"""[中线] §315 高收益方向 A–E（docs/strategy-research.md §315，结果出来前写定，不调参）：
  A SCW_T5 最小5只；B SCW_STR 小盘指数>MA20×1.03；C SCW_H40 持40日；D SMR_W 最小400里近20日跌最多10只；E SMM_W 最小400里近20日涨最多10只。
  复用 lt_research104.prep()（rk_s/scid/scid20/mvd/SCW）；同进程跑现役 SCW 当参照 SCW_ref；记录与 SCW 同周重合度。
用法：LPS_ST_SRC=db LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research120.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research104 import prep as prep104
c = pl.col
# name: (signal col, score col, hold, k_slots, max_new)
CAND = {"SCW_T5": ("T5", "sc_small", 20, 20, 5), "SCW_STR": ("STR", "sc_small", 20, 20, 10), "SCW_H40": ("H40", "sc_small", 40, 40, 10),
        "SMR_W": ("SMR", "sc_r", 20, 20, 10), "SMM_W": ("SMM", "sc_m", 20, 20, 10)}
REF = ("SCW_ref", ("SCW", "sc_small", 20, 20, 10))


def prep():
    df = prep104()
    wk = (c("trade_date").str.to_date().dt.weekday() == 5)
    on = (c("scid") > c("scid20")).fill_null(False)
    strong = (c("scid") / c("scid20") >= 1.03).fill_null(False)
    Up = ((c("rk_s") <= 400) & c("r20").is_not_null()).fill_null(False)
    df = df.with_columns(pl.when(Up).then(c("r20")).rank("ordinal").over("trade_date").alias("rk_lo"),
                         pl.when(Up).then(-c("r20")).rank("ordinal").over("trade_date").alias("rk_hi"),
                         (-c("r20")).fill_null(-1e9).alias("sc_r"), c("r20").fill_null(-1e9).alias("sc_m"))
    return df.with_columns(
        (wk & on & (c("rk_s") <= 5)).fill_null(False).alias("T5"),
        (wk & strong & (c("rk_s") <= 10)).fill_null(False).alias("STR"),
        (wk & on & (c("rk_s") <= 10)).fill_null(False).alias("H40"),
        (wk & on & (c("rk_lo") <= 10)).fill_null(False).alias("SMR"),
        (wk & on & (c("rk_hi") <= 10)).fill_null(False).alias("SMM")).sort("code", "trade_date")


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
        info = {}
        for n, (sig, _, _, _, _) in CAND.items():
            o = w.filter(c(sig)).group_by("trade_date").agg(pl.len().alias("a"), c("SCW").sum().alias("b"))
            info[n] = {"sigdays": o.height}
            if o.height:
                sel = w.filter(c(sig))
                info[n].update({"avg_n": round(float(o["a"].mean()), 2), "ovl_SCW": round(float((o["b"] / o["a"]).mean()), 3),
                                "r20_med": round(float(sel["r20"].median()), 3), "mvd_med_yi": round(float(sel["mvd"].median()) / 1e4, 1), "n_codes": sel["code"].n_unique()})
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        del w
        for n, (sig, sc, h, k, mn) in list(CAND.items()) + [REF]:
            t, cur = run_real(df, sig, sc, fixed(h), s, e, k_slots=k, max_new=mn, max_hold=h)
            m = full_metrics(df, t, cur)
            if t is not None and t.height:
                m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
                if n != "SCW_ref":
                    t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{n}_{ph.split()[0]}.csv"))
            res.setdefault(n, {})[ph] = m
            print(n, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
