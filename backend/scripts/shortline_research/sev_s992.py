"""[短线研究 §992–§995] 第二批：事件驱动短线 + 闸门下的小盘低波组合（换结构，不再做无条件横截面打分持 5 日）。
预注册 docs/strategy-research.md §992–§995（规则、参数写死后提交，再跑任何收益；每个只一版，不扫参）。

公共：行情与事件旗标复用 lt_research25.prep()（= 现役中线同口径事件：G 业绩加速、FA 首次预喜、IO 期权激励草案首次、
  RP_r05 回购预案金额≥市值0.5%、BTP 大宗溢价成交；各旗标已含 liq；事件信号日 sd = 公告日当天或之后的第一个交易日）。
  ST 口径 = lt_research.build() 的 st_hist.parquet。小盘指数 scid/scid20、mvd、SCW 来自 lt_research23.scw()。
  not_lim = 当日收盘既非涨停也非跌停（距涨跌停价 >0.005，缺价按前收 ±9.8%/±19.8%）。
  re2 = ac_T / ac_{T-2} − 1（T = sd+1 时即 sd 前一日收盘到 sd 次日收盘，盘前/盘后公告都覆盖）。
  引擎 realistic.run_real：T+1 开盘买（开盘涨停＝一字买不进、不递补）、持 h 个交易日开盘卖、跌停锁死顺延、0.3% 成本；
  K=20、max_new=5（仅回测）。
  §992 CATA_STRONG_h5 ：T 的前一交易日为任一事件 sd & T 日 liq & re2≥5% & T 收盘 not_lim；按 re2 降序；持 5。
  §993 CATA_DIP_h10   ：T = sd（RP_r05 或 IO）& r20_T ≤ −15% & T 收盘 not_lim；按 r20 升序；持 10。
  §994 SLV10_h10      ：小盘指数 scid>scid20 & 股池 Us（amt20>3000万、上市>250日、非ST、非北交所、mvd 非空、pe_ttm>0）
                        & not_lim & vol20 非空；市值低、vol20 低两因子 Us∩条件 内分位等权，每日前 5；持 10。
  §995 CATA_LU_h3     ：T 的前一交易日为任一事件 sd & T 日 liq & T 收盘封涨停（close ≥ 涨停价 −0.005）；按 re2 降序；持 3。
  参照（非候选）：SCW_ref = 现役小市值周度近似（lt_research23.scw，持 20，K20/max_new10），用于 §994 的资金效率对照。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=<backend>:. python ../shortline_research/sev_s992.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
import lt_research25 as R25
c = pl.col
K, MAX_NEW = 20, 5
PH = dict(PH)
PH["EXT 2024-01~2025-09"] = ("2024-01-01", "2025-09-30")
IDEAS = {"CATA_STRONG_h5": ("S992", "sc992", 5), "CATA_DIP_h10": ("S993", "sc993", 10),
         "SLV10_h10": ("S994", "sc994", 10), "CATA_LU_h3": ("S995", "sc995", 3)}


def _pct(x, S):
    return pl.when(S).then(x).rank("average").over("trade_date") / pl.when(S).then(1).sum().over("trade_date")


def prep():
    df = R25.prep().sort("code", "trade_date")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    pc = c("close").shift(1).over("code")
    dl = pl.coalesce(c("down_limit"), pc * (1 - band))
    ul = pl.coalesce(c("up_limit"), pc * (1 + band))
    ev = (c("G") | c("FA") | c("IO") | c("RP_r05") | c("BTP")).fill_null(False)
    df = df.with_columns(
        ((c("close") > dl + 0.005) & (c("close") < ul - 0.005)).fill_null(False).alias("not_lim"),
        (c("close") >= ul - 0.005).fill_null(False).alias("lu_close"),
        (c("ac") / c("ac").shift(2).over("code") - 1).alias("re2"),
        (c("pct_chg") / 100).rolling_std(20).over("code").alias("vol20"),
        ev.alias("ev"))
    df = df.with_columns(c("ev").shift(1).over("code").fill_null(False).alias("ev_prev"))
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    Us = ((c("amt20") > 3e7) & (c("age") > 250) & ~c("st") & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
          & c("mvd").is_not_null() & (c("pe_ttm") > 0)).fill_null(False)
    S994 = (Us & (c("scid") > c("scid20")) & c("not_lim") & c("vol20").is_not_null()).fill_null(False)
    df = df.with_columns(Us.alias("Us"), S994.alias("S994c"))
    n994 = pl.when(c("S994c")).then(1).sum().over("trade_date")
    sc994 = ((1 - _pct(c("mvd"), c("S994c")) + 1 / n994) + (1 - _pct(c("vol20"), c("S994c")) + 1 / n994)) / 2
    df = df.with_columns(pl.when(c("S994c")).then(sc994).alias("sc994"),
                         pl.when(c("Us")).then(c("mvd")).rank("ordinal").over("trade_date").alias("rk_us"))
    df = df.with_columns(pl.when(c("S994c")).then(-c("sc994")).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns(
        (c("ev_prev") & c("liq") & (c("re2") >= 0.05) & c("not_lim")).fill_null(False).alias("S992"),
        (c("re2").fill_null(-9) - tie).alias("sc992"),
        ((c("RP_r05") | c("IO")) & (c("r20") <= -0.15) & c("not_lim")).fill_null(False).alias("S993"),
        (-c("r20").fill_null(9) - tie).alias("sc993"),
        (c("S994c") & (c("_rk") <= 5)).fill_null(False).alias("S994"),
        c("sc994").fill_null(-1e9),
        (c("ev_prev") & c("liq") & c("lu_close")).fill_null(False).alias("S995"),
        (c("re2").fill_null(-9) - tie).alias("sc995"),
    ).drop("_rk").sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0; mx = max(mx, k)
    return mx


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    keep = ["code", "trade_date", "ao", "ac", "lim_up_open", "lock_down", "liq", "total_mv", "r20", "re2", "mvd", "rk_us",
            "SCW", "sc_small"] + [x for v in IDEAS.values() for x in v[:2]]
    df = df.select(keep)
    od = os.path.dirname(out) or "."
    os.makedirs(od, exist_ok=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        for name, (sig, sc, h) in IDEAS.items():
            w = df.filter(c(sig) & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
            info = {}
            if w.height:
                info = {"sigdays": w["trade_date"].n_unique(), "n_sig": w.height, "uniq_codes": w["code"].n_unique(),
                        "re2_med": round(float(w["re2"].median()), 3), "r20_med": round(float(w["r20"].median()), 3),
                        "mvd_med_yi": round(float(w["mvd"].median()) / 1e4, 1)}
                if name == "SLV10_h10":
                    info["scw_overlap"] = round(float((w["rk_us"] <= 10).mean()), 3)
            res.setdefault("_info", {}).setdefault(name, {})[ph] = info
            print("INFO", name, ph, info, flush=True)
            t, cur = run_real(df, sig, sc, fixed(h), s, e, k_slots=K, max_new=MAX_NEW, max_hold=h)
            m = full_metrics(df, t, cur)
            if t is not None and t.height:
                m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
                t.write_csv(os.path.join(od, f"trades_{name}_{ph.split()[0]}.csv"))
            res.setdefault(name, {})[ph] = m
            print(name, ph, m, flush=True)
            json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
        if ph.split()[0] in ("R12", "N3", "EXT"):
            t, cur = run_real(df, "SCW", "sc_small", fixed(20), s, e, k_slots=20, max_new=10, max_hold=20)
            res.setdefault("SCW_ref", {})[ph] = full_metrics(df, t, cur)
            print("SCW_ref", ph, res["SCW_ref"][ph], flush=True)
            json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
