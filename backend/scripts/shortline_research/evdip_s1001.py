"""[短线研究 §1001–§1003] 第四批：「强事件 × 公告前已深跌」，换前三批没用过的事件源，持 5 日。
预注册 docs/strategy-research.md §1001–§1003（规则、参数写死后提交，再跑任何收益；每个只一版，不扫参）。

公共（深跌门槛与成交口径同 §993/§997/§1000，不另调）：
  行情/股池 lt_research.build()（liq：amt20>5000万、上市>250日、非ST、非北交所；ST 口径 st_hist.parquet）。
  深跌 = 信号日 T 的近 20 日复权涨幅 r20 ≤ −15%；T 收盘既非涨停也非跌停（距涨跌停价>0.005，缺价按前收±9.8%/±19.8%）。
  T = 公告日当天或之后第一个交易日（sd）；按 r20 升序（跌得越深越先，平手按代码）。
  引擎 realistic.run_real：T+1 开盘买（一字涨停买不进、不递补）、持 5 个交易日开盘卖、跌停锁死顺延、往返 0.3%；K=20、max_new=5。
  §1001 HN_DIP_h5    ：股东户数环比下降≥10%（lt_research6.holder_events：每 (股票,期末) 第一次公告，前一期须已公告，公告距期末≤120天）。
  §1002 EARLY_DIP_h5 ：正式财报比去年同报告期提前≥15天披露（lt_research47.early_events，§155/§425 口径）。
  §1003 DE_DIP_h5    ：股东减持公告，同 (股票,公告日) change_ratio 合计≥1% 总股本（§986 门槛）。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=<backend>:.:../shortline_research python ../shortline_research/evdip_s1001.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research6 import holder_events
import lt_research47 as R47
from evdip_s1001_count import de_events
c = pl.col
HOLD, K, MAX_NEW, R20_MAX = 5, 20, 5, -0.15
PH = dict(PH)
PH["EXT 2024-01~2025-09"] = ("2024-01-01", "2025-09-30")
IDEAS = {"HN_DIP_h5": "E_hn", "EARLY_DIP_h5": "E_early", "DE_DIP_h5": "E_de"}


def prep():
    df = build().sort("code", "trade_date")
    td = df.select("trade_date").unique().sort("trade_date")
    df = (df.join(holder_events(td), on=["code", "trade_date"], how="left")
            .join(R47.early_events(td).rename({"sd": "trade_date"}), on=["code", "trade_date"], how="left")
            .join(de_events(td), on=["code", "trade_date"], how="left")).sort("code", "trade_date")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    pc = c("close").shift(1).over("code")
    dl = pl.coalesce(c("down_limit"), pc * (1 - band))
    ul = pl.coalesce(c("up_limit"), pc * (1 + band))
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    df = df.with_columns(
        ((c("close") > dl + 0.005) & (c("close") < ul - 0.005)).fill_null(False).alias("not_lim"),
        (c("hn_chg") <= -0.10).fill_null(False).alias("E_hn"),
        c("early").is_not_null().alias("E_early"),
        c("de_cr").is_not_null().alias("E_de"),
        (-c("r20").fill_null(9) - tie).alias("sc_dip"))
    base = c("liq") & (c("r20") <= R20_MAX) & c("not_lim")
    return df.with_columns([(c(ev) & base).fill_null(False).alias(f"S_{n}") for n, ev in IDEAS.items()]).sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0; mx = max(mx, k)
    return mx


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    keep = ["code", "trade_date", "ao", "ac", "lim_up_open", "lock_down", "liq", "total_mv", "r20", "sc_dip"] + [f"S_{n}" for n in IDEAS]
    df = df.select(keep)
    od = os.path.dirname(out) or "."
    os.makedirs(od, exist_ok=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        for name in IDEAS:
            sig = f"S_{name}"
            w = df.filter(c(sig) & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
            info = {}
            if w.height:
                info = {"sigdays": w["trade_date"].n_unique(), "n_sig": w.height, "uniq_codes": w["code"].n_unique(),
                        "r20_med": round(float(w["r20"].median()), 3), "max_per_day": int(w.group_by("trade_date").len()["len"].max()),
                        "month_top": w.group_by(c("trade_date").str.slice(0, 7)).len().sort("len", descending=True).head(3).rows()}
            res.setdefault("_info", {}).setdefault(name, {})[ph] = info
            print("INFO", name, ph, info, flush=True)
            t, cur = run_real(df, sig, "sc_dip", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
            m = full_metrics(df, t, cur)
            if t is not None and t.height:
                m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
                t.write_csv(os.path.join(od, f"trades_{name}_{ph.split()[0]}.csv"))
            res.setdefault(name, {})[ph] = m
            print(name, ph, m, flush=True)
            json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
