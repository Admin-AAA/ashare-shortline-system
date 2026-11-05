"""[短线研究 §987–§991] 短线「因子选股器」框架：每天在全市场合格股池里按若干因子的横截面分位打分，取前 5，次日开盘买、持 5 日。
预注册 docs/strategy-research.md §987–§991（规则、参数写死后提交，再跑任何收益；不调参、不扫网格）。

公共口径（5 个想法共用，写死）：
  行情/股池复用 lt_research104.prep()（mvd 折算市值、小盘等权指数 scid/scid20；ST 口径 LPS_ST_SRC=db）。
  U = amt20>3000万 & 上市>250日 & 非ST & 非北交所 & mvd 非空 & 信号日 T 收盘既非涨停也非跌停（距涨跌停价 >0.005）
      & r5、vol20 非空。
  因子：r5 = ac_T/ac_{T-5}-1；vol20 = 近 20 日日收益标准差；to20 = amt20/(mvd×1e4)（成交额/市值，换手代理）；
        shr = amt5/amt20（近 5 日均额/近 20 日均额，缩量程度）；flow5 = 近 5 日主力净流入合计/近 5 日成交额合计
        （capital_flow.main_net_inflow_yi，5 日内任一天缺数则为空）；r5_ind = r5 − 同日同行业（U 内）r5 均值。
  打分：每个因子在该想法的候选集 S 内按日求分位（0~1，越好越高），score = 各因子分位等权平均；每日取 score 前 5。
  引擎 realistic.run_real：T+1 开盘买（开盘涨停买不进、不递补）、持 5 个交易日、开盘卖、跌停锁死顺延、往返成本 0.3%；
  K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：cd backend/scripts/midterm && LPS_ST_SRC=db LT_START=.. LT_END=.. S987_FLOW=<capital_flow.parquet> \
      PYTHONPATH=<backend>:. python ../shortline_research/saf_s987.py "<phase>" out.json"""
import sys, json, os
from pathlib import Path
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research104 import prep as prep104
c = pl.col
HOLD, K, MAX_NEW, TOPN = 5, 20, 5, 5
PH = dict(PH)
PH["EXT 2024-01~2025-09"] = ("2024-01-01", "2025-09-30")
B = Path(__file__).resolve().parents[2]
FLOW = os.environ.get("S987_FLOW") or str(B / "output/research/s280/capital_flow.parquet")
IND = os.environ.get("S987_IND") or str(B / "data/industry_cache.json")

# 想法定义：name -> (候选集列, [(因子列, 方向)])；方向 "lo" = 越小越好，"hi" = 越大越好
IDEAS = {
    "SAF4_h5": ("S_saf4", [("mvd", "lo"), ("vol20", "lo"), ("to20", "lo"), ("r5", "lo")]),          # §987
    "FLOWREV_h5": ("S_flow", [("flow5", "hi"), ("r5", "lo")]),                                      # §988
    "SHRINKPB_h5": ("S_shr", [("r5", "lo"), ("shr", "lo")]),                                         # §989
    "INDREV_h5": ("S_ind", [("r5_ind", "lo"), ("vol20", "lo")]),                                     # §990
    "SAF6_h5": ("S_saf6", [("mvd", "lo"), ("vol20", "lo"), ("to20", "lo"), ("r5", "lo"),
                           ("flow5", "hi"), ("shr", "lo")]),                                         # §991
}


def _pct(x, S):
    """S 内按日升序分位（0~1]；S 外为空。"""
    return pl.when(S).then(x).rank("average").over("trade_date") / pl.when(S).then(1).sum().over("trade_date")


def prep():
    df = prep104().sort("code", "trade_date")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    pc = c("close").shift(1).over("code")
    dl = pl.coalesce(c("down_limit"), pc * (1 - band))
    ul = pl.coalesce(c("up_limit"), pc * (1 + band))
    df = df.with_columns(
        (c("ac") / c("ac").shift(5).over("code") - 1).alias("r5"),
        (c("pct_chg") / 100).rolling_std(20).over("code").alias("vol20"),
        c("amount").rolling_mean(5).over("code").alias("amt5"),
        ((c("close") > dl + 0.005) & (c("close") < ul - 0.005)).fill_null(False).alias("not_lim"))
    df = df.with_columns((c("amt20") / (c("mvd") * 1e4)).alias("to20"), (c("amt5") / c("amt20")).alias("shr"))
    # 资金流（只读本地导出）
    f = pl.read_parquet(FLOW, columns=["code", "trade_date", "main_net"])
    df = df.join(f, on=["code", "trade_date"], how="left").sort("code", "trade_date")
    df = df.with_columns((c("main_net") * 1e8).rolling_sum(5).over("code").alias("mn5"),
                         c("amount").rolling_sum(5).over("code").alias("am5"))
    df = df.with_columns((c("mn5") / c("am5")).alias("flow5")).drop("mn5", "am5")
    # 行业（当前行业映射，非时点）
    ind = json.load(open(IND))
    df = df.join(pl.DataFrame({"code": list(ind), "ind": list(ind.values())}), on="code", how="left").sort("code", "trade_date")
    U = ((c("amt20") > 3e7) & (c("age") > 250) & ~c("st") & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
         & c("mvd").is_not_null() & c("not_lim") & c("r5").is_not_null() & c("vol20").is_not_null()).fill_null(False)
    df = df.with_columns(U.alias("U"))
    df = df.with_columns(pl.when(c("U") & c("ind").is_not_null()).then(c("r5")).mean().over(["trade_date", "ind"]).alias("r5_im"),
                         pl.when(c("U") & c("ind").is_not_null()).then(1).sum().over(["trade_date", "ind"]).alias("ind_n"))
    df = df.with_columns((c("r5") - c("r5_im")).alias("r5_ind"))
    gate = (c("scid") > c("scid20"))
    df = df.with_columns(
        (c("U") & (c("pe_ttm") > 0) & gate).fill_null(False).alias("S_saf4"),
        (c("U") & c("flow5").is_not_null()).fill_null(False).alias("S_flow"),
        (c("U") & (c("ac") > c("m60")) & (c("r60") > 0) & (c("r5") < 0) & c("shr").is_not_null()).fill_null(False).alias("S_shr"),
        (c("U") & c("ind").is_not_null() & (c("ind_n") >= 5)).fill_null(False).alias("S_ind"),
        (c("U") & (c("pe_ttm") > 0) & gate & c("flow5").is_not_null() & c("shr").is_not_null()).fill_null(False).alias("S_saf6"))
    for name, (S, facs) in IDEAS.items():
        parts = [(_pct(c(f), c(S)) if d == "hi" else 1 - _pct(c(f), c(S)) + 1 / pl.when(c(S)).then(1).sum().over("trade_date"))
                 for f, d in facs]
        sc = pl.sum_horizontal(parts) / len(parts)
        df = df.with_columns(pl.when(c(S)).then(sc).alias(f"sc_{name}"))
        df = df.with_columns(pl.when(c(S)).then(-c(f"sc_{name}")).rank("ordinal").over("trade_date").alias("_rk"))
        df = df.with_columns((c(S) & (c("_rk") <= TOPN)).fill_null(False).alias(f"sig_{name}"),
                             c(f"sc_{name}").fill_null(-1e9)).drop("_rk")
    return df.sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0; mx = max(mx, k)
    return mx


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    keep = ["code", "trade_date", "ao", "ac", "lim_up_open", "lock_down", "liq", "total_mv", "r5", "vol20", "mvd"]
    df = df.select(keep + [f"sig_{n}" for n in IDEAS] + [f"sc_{n}" for n in IDEAS])
    od = os.path.dirname(out) or "."
    os.makedirs(od, exist_ok=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        for name in IDEAS:
            sig = f"sig_{name}"
            w = df.filter(c(sig) & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
            info = {}
            if w.height:
                info = {"sigdays": w["trade_date"].n_unique(), "n_sig": w.height,
                        "r5_med": round(float(w["r5"].median()), 3), "vol20_med": round(float(w["vol20"].median()), 4),
                        "mvd_med_yi": round(float(w["mvd"].median()) / 1e4, 1), "uniq_codes": w["code"].n_unique()}
            res.setdefault("_info", {}).setdefault(name, {})[ph] = info
            print("INFO", name, ph, info, flush=True)
            t, cur = run_real(df, sig, f"sc_{name}", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
            m = full_metrics(df, t, cur)
            if t is not None and t.height:
                m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
                t.write_csv(os.path.join(od, f"trades_{name}_{ph.split()[0]}.csv"))
            res.setdefault(name, {})[ph] = m
            print(name, ph, m, flush=True)
            json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
