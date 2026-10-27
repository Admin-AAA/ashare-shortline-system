"""[短线研究 §332 / §333] 预注册 docs/strategy-research.md §332/§333，结果出来前写定，不调参。
  §332 LVZ_h5「低波动股被错杀」：平时很稳的股票，5 天跌了平时波动的 2.5 倍以上 → 次日开盘买、持 5 日。
    LV 池 = liq（amt20>5000万 & 上市>250日 & 非ST & 非北交所）& pe_ttm>0 & vol120 非空；
    vrk = LV 池内 vol120 升序百分位（0~1），低波 = vrk ≤ 0.30；
    r5 = ac_T / ac_{T−5} − 1；z5 = r5 / (vol120 × √5)；
    候选 = 低波 & z5 ≤ −2.5 & T 日收盘未在跌停（close > 跌停价 + 0.005）；同日按 z5 升序取前 5。
  §333 FDV_h5「好消息却跌：业绩预喜公告后首个交易日收跌」：
    事件 = lt_research20 的 F_any（首次预告、type∈{预增,扭亏,略增}、p_change_min>0、net_profit_min>0），
    信号日 sd = ann_date 起第一个交易日；候选 = sd 日 liq & sd 当日 pct_chg < 0；同日按 sd 当日 pct_chg 升序（跌得最多）取前 5。
  引擎 realistic：T+1 开盘买（开盘涨停买不进、不递补）、持 5 日、跌停锁死顺延、0.3% 成本；K=20、max_new=5（仅回测，实盘不设仓位上限）。
  同进程参照（非候选）：F_any_h20（现役 mid 预告预喜，持 20 日）。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/lvz_fdv_s332.py "<phase>" out.json"""
import sys, json, os, math
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research20 import prep as prep20
c = pl.col
HOLD, K, MAX_NEW = 5, 20, 5
RUNS = (("LVZ_h5", "LVZ", "sc_lvz", 5, 5), ("FDV_h5", "FDV", "sc_fdv", 5, 5), ("F_any_h20", "FA", "sc_a", 20, 5))
SAVE = ("LVZ_h5", "FDV_h5")


def prep():
    df = prep20().sort("code", "trade_date")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    pc = c("close").shift(1).over("code")
    dl = pl.coalesce(c("down_limit"), pc * (1 - band))
    df = df.with_columns((c("close") <= dl + 0.005).fill_null(False).alias("ldc"),
                         (c("ac") / c("ac").shift(5).over("code") - 1).alias("r5"))
    lv = (c("liq") & (c("pe_ttm") > 0) & c("vol120").is_not_null() & (c("vol120") > 0)).fill_null(False)
    df = df.with_columns(lv.alias("lv"))
    vrk = pl.when(c("lv")).then(c("vol120")).rank("average").over("trade_date") / pl.when(c("lv")).then(1).sum().over("trade_date")
    df = df.with_columns(vrk.alias("vrk"), (c("r5") / (c("vol120") * math.sqrt(5))).alias("z5"))
    cand = (c("lv") & (c("vrk") <= 0.30) & (c("z5") <= -2.5) & ~c("ldc")).fill_null(False)
    df = df.with_columns(cand.alias("cand_lvz"))
    df = df.with_columns(pl.when(c("cand_lvz")).then(c("z5")).rank("ordinal").over("trade_date").alias("_rk1"))
    fd = (c("FA") & (c("pct_chg") < 0)).fill_null(False)
    df = df.with_columns(fd.alias("cand_fdv"))
    df = df.with_columns(pl.when(c("cand_fdv")).then(c("pct_chg")).rank("ordinal").over("trade_date").alias("_rk2"))
    return df.with_columns(
        (c("cand_lvz") & (c("_rk1") <= MAX_NEW)).fill_null(False).alias("LVZ"),
        (-c("z5")).fill_null(-1e9).alias("sc_lvz"),
        (c("cand_fdv") & (c("_rk2") <= MAX_NEW)).fill_null(False).alias("FDV"),
        (-c("pct_chg")).fill_null(-1e9).alias("sc_fdv"),
    ).drop("_rk1", "_rk2").sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0; mx = max(mx, k)
    return mx


def desc(w, col):
    if not w.height:
        return {"n_sig": 0}
    return {"n_sig": w.height, "sigdays": w["trade_date"].n_unique(), "n_codes": w["code"].n_unique(),
            "sig_pct_med": round(float(w["pct_chg"].median()), 2), "r5_med": round(float(w["r5"].median()), 3),
            "r20_med": round(float(w["r20"].median()), 3), "z5_med": round(float(w["z5"].median()), 2) if w["z5"].null_count() < w.height else None,
            "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64, strict=False).median()) / 1e4, 1),
            "month_top": w.group_by(c("trade_date").str.slice(0, 7)).len().sort("len", descending=True).head(3).rows()}


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        w = df.filter(c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"LVZ": desc(w.filter(c("LVZ")), "LVZ"), "LVZ_cand_all": int(w["cand_lvz"].sum()),
                "FDV": desc(w.filter(c("FDV")), "FDV"), "FDV_cand_all": int(w["cand_fdv"].sum()), "FA_events": int(w["FA"].sum())}
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, json.dumps(info, ensure_ascii=False), flush=True)
        del w
        trades = {}
        for n, sig, sc, h, mn in RUNS:
            t, cur = run_real(df, sig, sc, fixed(h), s, e, k_slots=K, max_new=mn, max_hold=h)
            m = full_metrics(df, t, cur)
            if t is not None and t.height:
                m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
                m["per_day"] = round(m["avg"] / max(m["days"], 1e-9), 3)
                if n in SAVE:
                    t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{n}_{ph.split()[0]}.csv"))
            trades[n] = t
            res.setdefault(n, {})[ph] = m
            print(n, ph, m, flush=True)
        a, b = trades.get("FDV_h5"), trades.get("F_any_h20")
        if a is not None and a.height and b is not None and b.height:
            ov = a.join(b.select("code", "entry"), on=["code", "entry"], how="semi").height / a.height
            res["FDV_h5"][ph]["ovl_F_any_h20"] = round(ov, 3)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
