"""[短线研究 §328]「连续 ≥2 天跌停收盘后首次没有跌停收盘 → 次日开盘买、持 5 日」LDO_h5（利空连续跌停后的首次打开：恐慌出清、卖盘衰竭后的反弹）。
预注册 docs/strategy-research.md §328，结果出来前写定，不调参。
  跌停收盘：close ≤ coalesce(down_limit, 前收×(1−band)) + 0.005，band 同 realistic.add_limits（300/688 0.198，其余 0.098）。
  事件：T−1、T−2 均跌停收盘（连续 ≥2 天，含一字），T 日收盘 > 跌停价+0.005（首次没收在跌停）。
  条件：T 日 liq（amt20>5000万 & 上市>250日 & 非ST & 非北交所）。同日按 T 日涨跌幅（复权 pct_chg）降序取前 5（反弹最强优先）。
  引擎 realistic：T+1 开盘买（开盘涨停买不进、不递补）、持 5 日、跌停锁死顺延、0.3% 成本；K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/ldo_s328.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
HOLD, K, MAX_NEW, NAME = 5, 20, 5, "LDO_h5"


def prep():
    df = build().sort("code", "trade_date")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    pc = c("close").shift(1).over("code")
    dl = pl.coalesce(c("down_limit"), pc * (1 - band))
    df = df.with_columns((c("close") <= dl + 0.005).fill_null(False).alias("ldc"))
    l1, l2 = c("ldc").shift(1).over("code").fill_null(False), c("ldc").shift(2).over("code").fill_null(False)
    df = df.with_columns((l1 & l2 & ~c("ldc")).alias("ev"))
    # 连续跌停天数（T 之前），只作描述
    grp = (~c("ldc")).cum_sum().over("code")
    df = df.with_columns(c("ldc").cast(pl.Int32).cum_sum().over(["code", grp]).alias("_run"))
    df = df.with_columns(c("_run").shift(1).over("code").alias("streak_ld"))
    cand = (c("ev") & c("liq")).fill_null(False)
    df = df.with_columns(cand.alias("cand"))
    df = df.with_columns(pl.when(c("cand")).then(-c("pct_chg")).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns((c("cand") & (c("_rk") <= MAX_NEW)).fill_null(False).alias("LDO"),
                           c("pct_chg").fill_null(-1e9).alias("sc_ldo")).drop("_rk", "_run").sort("code", "trade_date")


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
        w = df.filter(c("LDO") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        a = df.filter(c("ev") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"n_sig": w.height, "n_ev_all": a.height}
        if w.height:
            info.update({"sigdays": w["trade_date"].n_unique(), "n_codes": w["code"].n_unique(),
                         "sigday_pct_med": round(float(w["pct_chg"].median()), 2),
                         "streak_med": float(w["streak_ld"].median()), "r20_med": round(float(w["r20"].median()), 3),
                         "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64, strict=False).median()) / 1e4, 1),
                         "month_top": w.group_by(c("trade_date").str.slice(0, 7)).len().sort("len", descending=True).head(3).rows()})
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "LDO", "sc_ldo", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
