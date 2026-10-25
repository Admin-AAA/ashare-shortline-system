"""[短线研究 §313]「小盘低位首板 → 次日开盘买、持 5 日」SFB_h5（邢不行视频《找出历史11万次涨停，考察未来收益：量化打板赚钱吗？》+ 小市值框架的小盘趋势闸门）。
预注册 docs/strategy-research.md §313，结果出来前写定，不调参。
  复用 lt_research104.prep()（mvd、scid/scid20、rk_s；ST 口径 LPS_ST_SRC=db）。pre = close/(1+pct_chg/100)；ul = coalesce(up_limit, pre×(1+9.8%/19.8%))。
  lu = close ≥ ul − 0.005（收盘封涨停）；首板 = 前 20 个交易日无 lu；低位 = ac_{T−1}/ac_{T−21} − 1 ≤ 10%；非一字板 = high − low > 0.005。
  候选：rk_s ≤ 400 & scid > scid20 & lu & 首板 & 低位 & 非一字板；每个信号日按 mvd 升序取前 5。
  realistic：T+1 开盘买（开盘涨停买不进、不递补）、持 5 日、跌停锁死顺延、0.3% 成本；K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：cd backend/scripts/midterm && LPS_ST_SRC=db LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/sfb_s313.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research104 import prep as prep104
c = pl.col
HOLD, K, MAX_NEW, NAME = 5, 20, 5, "SFB_h5"


def prep():
    df = prep104().sort("code", "trade_date")
    pre = c("close") / (1 + c("pct_chg") / 100)
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    df = df.with_columns(pre.alias("pre"))
    ul = pl.coalesce(c("up_limit"), c("pre") * (1 + band))
    df = df.with_columns((c("close") >= ul - 0.005).fill_null(False).alias("lu"))
    df = df.with_columns(c("lu").cast(pl.Int32).shift(1).rolling_sum(20).over("code").alias("lu20p"),
                         (c("ac").shift(1).over("code") / c("ac").shift(21).over("code") - 1).alias("r20p"))
    cand = ((c("rk_s") <= 400) & (c("scid") > c("scid20")) & c("lu") & (c("lu20p") == 0)
            & (c("r20p") <= 0.10) & ((c("high") - c("low")) > 0.005)).fill_null(False)
    df = df.with_columns(cand.alias("cand")).with_columns(pl.when(c("cand")).then(c("mvd")).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns((c("cand") & (c("_rk") <= 5)).fill_null(False).alias("SFB"),
                           (-c("_rk").cast(pl.Float64)).fill_null(-1e9).alias("sc_sfb")).drop("_rk").sort("code", "trade_date")


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
        w = df.filter(c("SFB") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {}
        if w.height:
            nx = df.select("code", "trade_date", c("lim_up_open").shift(-1).over("code").alias("nx_lu")).join(w.select("code", "trade_date"), on=["code", "trade_date"])
            info = {"sigdays": w["trade_date"].n_unique(), "n_sig": w.height, "r20p_med": round(float(w["r20p"].median()), 3),
                    "mvd_med_yi": round(float(w["mvd"].median()) / 1e4, 1), "next_open_limit_up_share": round(float(nx["nx_lu"].fill_null(False).mean()), 3)}
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "SFB", "sc_sfb", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
