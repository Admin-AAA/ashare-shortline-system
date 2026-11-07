"""[短线研究 §1013–§1017] 市场级 RSI 恐慌买入（等权全市场 RSI2/RSI6/RSI14 × 宽基/超跌/强势）。

预注册 docs/strategy-research.md §1013–§1017（2026-11-06 23:58 CST 写于跑任何收益之前，规则写定后不改）。

市场 RSI（T 收盘可得、因果）：
  等权日收益 = 当日 amount>0、非北交所全部股票 pct_chg 截尾 ±20 的均值；样本数 <1000 的日期不参与。
  等权指数 = 日收益累乘（lt_data 自带 420 天预热）；Wilder RSI(2/6/14)。
信号：T 收盘 RSI 低于阈值 → T+1 开盘买；持 3/5 日；收盘跌破入场开盘×0.93 次日开盘止损；
      一字涨停买不进、不递补；跌停锁死顺延；往返 0.3%。
股池 liq：非 ST（PIT st_hist）、非北交所、上市>250 日、20 日均额>5000 万；信号日收盘未封涨停。

版本：
  §1013 MRSI2_BROAD_h3   rsi2<10，amt20 前 10，持 3，max_new=10
  §1014 MRSI2_DIP_h3     rsi2<10，r20 最低 5，持 3，max_new=5
  §1015 MRSI2_STRONG_h3  rsi2<10，r20 最高 5，持 3，max_new=5
  §1016 MRSI6_BROAD_h5   rsi6<20，amt20 前 10，持 5，max_new=10
  §1017 MRSI14_BROAD_h5  rsi14<30，amt20 前 10，持 5，max_new=10
对照（诊断）：
  ALL_BROAD_h3 / ALL_DIP_h3 / ALL_STRONG_h3：同选股每天买，K=100，不看 RSI。

用法：cd backend/scripts/midterm
  LT_START=.. LT_END=.. PYTHONPATH=<backend>:.:../shortline_research \
  ../../.venv/bin/python ../shortline_research/mrsi_s1013.py "<phase>" out.json
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import polars as pl

from lt_research import PH, build, fixed
from lt_research46 import full_metrics
from realistic import run_real

c = pl.col
K = 20
PHASES = dict(PH)
PHASES["EXT 2024-01~2025-09"] = ("2024-01-01", "2025-09-30")
PHASES["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")

CONFIGS = (
    # name, signal col, score col, hold, max_new, k_slots
    ("MRSI2_BROAD_h3", "S_MRSI2_BROAD", "sc_amt", 3, 10, K),
    ("MRSI2_DIP_h3", "S_MRSI2_DIP", "sc_dip", 3, 5, K),
    ("MRSI2_STRONG_h3", "S_MRSI2_STRONG", "sc_strong", 3, 5, K),
    ("MRSI6_BROAD_h5", "S_MRSI6_BROAD", "sc_amt", 5, 10, K),
    ("MRSI14_BROAD_h5", "S_MRSI14_BROAD", "sc_amt", 5, 10, K),
    ("ALL_BROAD_h3", "S_ALL_BROAD", "sc_amt", 3, 10, 100),
    ("ALL_DIP_h3", "S_ALL_DIP", "sc_dip", 3, 5, 100),
    ("ALL_STRONG_h3", "S_ALL_STRONG", "sc_strong", 3, 5, 100),
)


def _rsi_wilder(x: np.ndarray, n: int) -> np.ndarray:
    """Wilder RSI（因果）。x 为指数收盘序列；前 n 个涨跌幅均值做初值。"""
    delta = np.diff(x)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    ag = np.full(len(x), np.nan)
    al = np.full(len(x), np.nan)
    if len(x) <= n:
        return ag
    ag[n] = gain[:n].mean()
    al[n] = loss[:n].mean()
    for i in range(n + 1, len(x)):
        ag[i] = (ag[i - 1] * (n - 1) + gain[i - 1]) / n
        al[i] = (al[i - 1] * (n - 1) + loss[i - 1]) / n
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100.0 - 100.0 / (1.0 + ag / al)
    out[(al == 0) & (ag > 0)] = 100.0
    out[(ag == 0) & (al > 0)] = 0.0
    out[(ag == 0) & (al == 0)] = 50.0
    return out


def market_rsi(df: pl.DataFrame) -> pl.DataFrame:
    """等权全市场指数 + RSI2/6/14；口径与 app/services/market_trend.py 对齐。"""
    mkt = (
        df.filter((c("amount") > 0) & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"]))
        .with_columns(c("pct_chg").clip(-20, 20).alias("_r"))
        .group_by("trade_date")
        .agg(c("_r").mean().alias("mkt_ret"), pl.len().alias("_n"))
        .filter(c("_n") >= 1000)
        .sort("trade_date")
    )
    idx = (1.0 + mkt["mkt_ret"].to_numpy() / 100.0).cumprod()
    out = mkt.select("trade_date", "mkt_ret")
    for n in (2, 6, 14):
        out = out.with_columns(pl.Series(f"rsi{n}", _rsi_wilder(idx, n)))
    return out


def prep() -> pl.DataFrame:
    df = build().sort("code", "trade_date")
    mr = market_rsi(df)
    df = df.join(mr, on="trade_date", how="left")

    band = (
        pl.when(c("code").str.starts_with("688"))
        .then(0.198)
        .when(
            (c("code").str.starts_with("300") | c("code").str.starts_with("301"))
            & (c("trade_date") >= "2020-08-24")
        )
        .then(0.198)
        .otherwise(0.098)
    )
    pc = c("close").shift(1).over("code")
    ul = pl.coalesce(c("up_limit"), pc * (1 + band))
    not_lu = (c("close") < ul - 0.005).fill_null(False)

    rsi2, rsi6, rsi14 = c("rsi2"), c("rsi6"), c("rsi14")
    base = c("liq") & not_lu
    has_r20 = c("r20").is_not_null()

    sig_broad2 = base & (rsi2 < 10)
    sig_dip2 = base & (rsi2 < 10) & has_r20
    sig_strong2 = base & (rsi2 < 10) & has_r20
    sig_broad6 = base & (rsi6 < 20)
    sig_broad14 = base & (rsi14 < 30)

    df = df.with_columns(
        sig_broad2.fill_null(False).alias("S_MRSI2_BROAD"),
        sig_dip2.fill_null(False).alias("S_MRSI2_DIP"),
        sig_strong2.fill_null(False).alias("S_MRSI2_STRONG"),
        sig_broad6.fill_null(False).alias("S_MRSI6_BROAD"),
        sig_broad14.fill_null(False).alias("S_MRSI14_BROAD"),
        base.fill_null(False).alias("S_ALL_BROAD"),
        (base & has_r20).fill_null(False).alias("S_ALL_DIP"),
        (base & has_r20).fill_null(False).alias("S_ALL_STRONG"),
        c("amt20").alias("sc_amt"),
        (-c("r20").fill_null(-9.0)).alias("sc_dip"),
        (c("r20").fill_null(-9.0)).alias("sc_strong"),
    )
    return df.sort("code", "trade_date")


def _streak(t: pl.DataFrame) -> int:
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0
        mx = max(mx, k)
    return mx


def event_study(df: pl.DataFrame, sig_cols: list[str], s: str, e: str) -> dict:
    """等权指数在信号日后 1/3/5/10 日 close-to-close 收益 vs 全样本（描述性）。"""
    md = (
        df.select("trade_date", "mkt_ret")
        .unique()
        .drop_nulls()
        .sort("trade_date")
    )
    if md.height < 15:
        return {}
    dates = md["trade_date"].to_list()
    idx = (1.0 + md["mkt_ret"].to_numpy() / 100.0).cumprod()
    pos = {d: i for i, d in enumerate(dates)}
    in_phase = [i for i, d in enumerate(dates) if s <= d <= e]
    out: dict = {}
    for col in sig_cols:
        sd = set(df.filter(c(col) & (c("trade_date") >= s) & (c("trade_date") <= e))["trade_date"].unique().to_list())
        sig_pos = [pos[d] for d in sd if d in pos]
        item: dict = {"signal_days": len(sig_pos)}
        for h in (1, 3, 5, 10):
            sig_r = [idx[i + h] / idx[i] - 1.0 for i in sig_pos if i + h < len(idx)]
            all_r = [idx[i + h] / idx[i] - 1.0 for i in in_phase if i + h < len(idx)]
            if not sig_r or not all_r:
                continue
            item[f"h{h}"] = {
                "sig_n": len(sig_r),
                "sig_mean": round(100.0 * float(np.mean(sig_r)), 2),
                "sig_win": round(100.0 * float(np.mean(np.array(sig_r) > 0)), 1),
                "all_n": len(all_r),
                "all_mean": round(100.0 * float(np.mean(all_r)), 2),
                "all_win": round(100.0 * float(np.mean(np.array(all_r) > 0)), 1),
                "diff": round(100.0 * float(np.mean(sig_r) - np.mean(all_r)), 2),
            }
        out[col] = item
    return out


def main() -> None:
    phase_arg, out = sys.argv[1], sys.argv[2]
    phases = [p for p in phase_arg.split(",") if p in PHASES]
    if not phases:
        raise SystemExit(f"unknown phase: {phase_arg}; choose from {list(PHASES)}")

    df = prep()
    print("panel", df.shape, flush=True)
    keep = ["code", "trade_date", "ao", "ac", "lim_up_open", "lock_down", "liq", "r20", "amt20",
            "total_mv", "mkt_ret", "rsi2", "rsi6", "rsi14"]
    keep += [x[1] for x in CONFIGS] + [x[2] for x in CONFIGS]
    keep = list(dict.fromkeys(keep))
    df = df.select([x for x in keep if x in df.columns])

    od = os.path.dirname(out) or "."
    os.makedirs(od, exist_ok=True)
    res = json.load(open(out)) if os.path.exists(out) else {}

    for ph in phases:
        s, e = PHASES[ph]
        res.setdefault("_event", {})[ph] = event_study(
            df, ["S_MRSI2_BROAD", "S_MRSI2_DIP", "S_MRSI2_STRONG", "S_MRSI6_BROAD", "S_MRSI14_BROAD"], s, e
        )
        for name, sig, score, hold, max_new, k_slots in CONFIGS:
            w = df.filter(c(sig) & (c("trade_date") >= s) & (c("trade_date") <= e))
            if name.startswith("MRSI"):
                info = {}
                if w.height:
                    info = {
                        "sigdays": int(w["trade_date"].n_unique()),
                        "n_sig": int(w.height),
                        "uniq_codes": int(w["code"].n_unique()),
                        "max_per_day": int(w.group_by("trade_date").len()["len"].max()),
                        "rsi_min": round(float(w["rsi2"].min()), 2),
                        "rsi_med": round(float(w["rsi2"].median()), 2),
                        "month_top": w.group_by(c("trade_date").str.slice(0, 7)).len().sort("len", descending=True).head(3).rows(),
                    }
                res.setdefault("_info", {}).setdefault(name, {})[ph] = info
                print("INFO", name, ph, info, flush=True)
            t, cur = run_real(df, sig, score, fixed(hold), s, e, k_slots=k_slots, max_new=max_new, max_hold=hold)
            m = full_metrics(df, t, cur)
            if t is not None and t.height:
                m["streak"] = float(_streak(t))
                m["p05"] = round(100.0 * float(t["ret"].quantile(0.05)), 2)
                t.write_csv(os.path.join(od, f"trades_{name}_{ph.split()[0]}.csv"))
            res.setdefault(name, {})[ph] = m
            print(name, ph, m, flush=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

    print("done ->", out, flush=True)


if __name__ == "__main__":
    main()
