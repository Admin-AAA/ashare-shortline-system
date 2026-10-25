"""[中线] §314「小盘低市销率」SPS_W_h20（邢不行小市值周度框架 + 市销率估值：
在盈利、流动的最小 400 只小票（rk_s≤400，小盘指数成分口径）里选市销率 PS_TTM 最低 10 只）— 预注册（docs/strategy-research.md §314，结果出来前写定，不调参）。
  复用 lt_research118.prep()（= lt_research104.prep() + SDY_W；SCW / LPS_W / SDY_W 三个现役参照列；ST 口径 LPS_ST_SRC=db）。
  ps_ttm 取自 basic_YYYY.parquet 周度快照，按 (code, bd) 与价格面板对齐（同 pe_ttm/dv_ttm 的 bd 快照日）。
  Up = rk_s<=400 & ps_ttm>0；rk_p = Up 内 ps_ttm 升序名次。SPS_W：周五 & 小盘等权指数>MA20 & rk_p<=10；次日开盘买、持 20 日。K=20、max_new=10（仅回测，实盘不设仓位上限）。
  同进程跑现役 SCW / SDY_W / LPS_W 作参照（不是候选），报同周重叠率；若 N3 与某现役平均重叠 ≥50%，需 N3 每笔高于同进程该参照才算通过。
用法：LPS_ST_SRC=db LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research119.py "<phase>" out.json"""
import sys, json, os
import polars as pl
import lt_data as L
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research118 import prep as prep118
c = pl.col
NAME = "SPS_W_h20"
REFS = (("SCW_ref", "SCW", "sc_small"), ("SDY_ref", "SDY_W", "sc_dy"), ("LPS_ref", "LPS_W", "sc_small"))


def ps_basic():
    start, end = L._range(None, None)
    fs = [f for f in sorted(L.D.glob("basic_[0-9]*.parquet")) if not start or f.stem[-4:] >= start[:4]]
    b = pl.concat([pl.read_parquet(f, columns=["ts_code", "trade_date", "ps_ttm"]) for f in fs], how="diagonal_relaxed")
    b = b.filter(L._win("trade_date", start, end, dash=False))
    return (b.select(c("ts_code").str.slice(0, 6).alias("code"), L._dash("trade_date").alias("bd"),
                     c("ps_ttm").cast(pl.Float64, strict=False)).unique(["code", "bd"]))


def prep():
    df = prep118().join(ps_basic(), on=["code", "bd"], how="left")
    wk = (c("trade_date").str.to_date().dt.weekday() == 5)
    Up = ((c("rk_s") <= 400) & (c("ps_ttm") > 0)).fill_null(False)
    df = df.with_columns(pl.when(Up).then(c("ps_ttm")).rank("ordinal").over("trade_date").alias("rk_p"),
                         (-c("ps_ttm")).fill_null(-1e9).alias("sc_ps"))
    on = c("scid") > c("scid20")
    return df.with_columns((wk & on & (c("rk_p") <= 10)).fill_null(False).alias("SPS_W")).sort("code", "trade_date")


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
        o = w.filter(c("SPS_W")).group_by("trade_date").agg(pl.len().alias("a"), c("SCW").sum().alias("b1"), c("SDY_W").sum().alias("b2"), c("LPS_W").sum().alias("b3"))
        info = {"sigdays": o.height}
        if o.height:
            sel = w.filter(c("SPS_W"))
            info.update({"avg_n": round(float(o["a"].mean()), 2), "ovl_SCW": round(float((o["b1"] / o["a"]).mean()), 3),
                         "ovl_SDY": round(float((o["b2"] / o["a"]).mean()), 3), "ovl_LPS": round(float((o["b3"] / o["a"]).mean()), 3),
                         "ps_med": round(float(sel["ps_ttm"].median()), 2), "pe_med": round(float(sel["pe_ttm"].median()), 1),
                         "pb_med": round(float(sel["pb"].median()), 2), "dv_med": round(float(sel["dv_ttm"].median()), 2),
                         "rk_s_med": round(float(sel["rk_s"].median()), 0), "mvd_med_yi": round(float(sel["mvd"].median()) / 1e4, 1),
                         "close_med": round(float(sel["close"].median()), 2), "r20_med": round(float(sel["r20"].median()), 3), "n_codes": sel["code"].n_unique()})
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        del w
        for n, sig, sc in ((NAME, "SPS_W", "sc_ps"),) + REFS:
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
