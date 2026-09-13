"""本轮（feat/derived-tail-veto）预筛：未测衍生因子族 + 日线存量指标残列。

动机（为何本轮不同，见报告）：
- L-093 证明「线性筛查失败 ≠ 因子无效」：ivol20 的逐月同号只有 0.42（远低于 L-065 的 0.8 门槛），
  仍成为唯一通过生产同构三窗的日线横截面否决。原因是 edge 在 **regime 条件 × 极端尾部 × 大亏率**，
  而线性 IC / 逐月均值同号恰好看不见它。
- 因此本轮不再用「逐月均值同号 ≥0.8」做闸门，改用 **尾部大亏率（fwd5 ≤ -5%）在各分位的单调性**
  作为第一道筛（三窗同向），把通过者送去生产同构 canonical 三窗终审。
- 覆盖两类从没被真正测过的东西：
  A) 隔夜/日内收益分解族（on/id/on_minus_id/on_share/on_vol）——此前只在「逐月均值同号」线性闸门下被否，
     从未做尾部/regime 条件检验；是 daily_kbars 之外需自构的新构念（L-081 只筛存量列）。
  B) 非对称/特质崩盘风险族——L-093 预筛里 downvol20 因 min_periods=16 > 20 日内的下跌日数而**全空**
     （静默失效、从未真正检验）；补上 downvol / resid_skew / resid_max / downside beta。
  C) daily_kbars 存量指标里从未被单独筛过的残列（asi/bbi/brar/dpo/mtm/obv/psy/trix/vr/kdj/macd/cci/boll/
     ema/ma_volume/chip_cost/breakout/max_dd60/holder_pct/profit_efficiency）——闭合 L-082 的列级覆盖缺口。

只读研究脚本；面板缓存到 output/research/si_rf_panel.pkl。
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

warnings.filterwarnings("ignore")
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))
from app.db.session import SessionLocal  # noqa: E402

OUT = B / "output" / "research"
PANEL = OUT / "si_rf_panel.pkl"

WINDOWS = {
    "W1": ("2025-01-15", "2026-01-16"),
    "W2": ("2026-02-09", "2026-08-05"),
    "W3": ("2026-08-07", "2026-09-02"),
}
LOAD_FROM = "2024-09-01"
KEEP_FROM = "2025-01-06"

IND_COLS = [
    "asi", "asit", "bbi", "brar_ar", "brar_br", "dpo", "ema_12", "ema_50",
    "ma_volume_5", "ma_volume_10", "mtm", "obv", "psy", "trix", "vr",
    "wr_6", "wr_10", "kdj_k", "kdj_d", "kdj_j", "macd_dif", "macd_dea", "macd_bar",
    "cci", "boll_upper", "boll_mid", "boll_lower", "rsi_6", "rsi_12", "rsi_24",
    "bias_5", "bias_10", "bias_20", "max_dd20", "max_dd60", "breakout20", "breakout60",
    "atr", "chip_cost_15", "chip_cost_50", "chip_cost_85", "chip_weight_avg",
    "chip_winner_rate", "upper_shadow_count20", "holder_pct", "profit_efficiency",
    "vol_ratio_5", "vol_ratio_20", "turnover_rate",
]


def load_raw() -> pd.DataFrame:
    with SessionLocal() as s:
        k = pd.DataFrame(s.execute(text(
            f"""SELECT code, trade_date, open, high, low, close, volume, amount, pct_chg,
                       {', '.join(IND_COLS)}
                FROM daily_kbars
                WHERE trade_date >= :s AND close > 0 AND high > 0 AND low > 0 AND open > 0
                  AND code NOT LIKE '8%' AND code NOT LIKE '4%' AND code NOT LIKE '920%'
                  AND code NOT IN (SELECT code FROM stocks WHERE name LIKE '%ST%')"""),
            {"s": LOAD_FROM}).mappings().all())
        idx = pd.DataFrame(s.execute(text(
            "SELECT ts_code, trade_date, pct_chg FROM index_daily WHERE ts_code='000905.SH'")
        ).mappings().all())
    return k, idx


def build_panel() -> pd.DataFrame:
    k, idx = load_raw()
    print(f"loaded raw rows={len(k):,}", flush=True)
    f32 = np.float32
    base = ["open", "high", "low", "close", "volume", "amount", "pct_chg"] + IND_COLS
    for c in base:
        k[c] = pd.to_numeric(k[c], errors="coerce").astype(f32)
    mkt = idx.assign(mkt=(pd.to_numeric(idx.pct_chg, errors="coerce") / 100).astype(f32))[["trade_date", "mkt"]]
    k = k.merge(mkt, on="trade_date", how="left").sort_values(["code", "trade_date"]).reset_index(drop=True)

    parts = []
    n_grp = 0
    for code, blk in k.groupby("code", sort=False):
        n_grp += 1
        if n_grp % 500 == 0:
            print(f"  ... {n_grp} codes", flush=True)
        blk = blk.reset_index(drop=True)
        o, h, l, cl = blk.open, blk.high, blk.low, blk.close
        # 用 pct_chg 反推前收（对除权精确），隔夜口径与 L-084 一致
        pre = (cl / (1.0 + blk.pct_chg / 100.0)).astype(f32)
        r = (blk.pct_chg / 100.0).astype(f32)
        m = blk.mkt.astype(float)
        rf = r.astype(float)

        on = (o / pre - 1.0).astype(f32)            # 隔夜
        idr = (cl / o - 1.0).astype(f32)            # 日内
        d = pd.DataFrame({"code": code, "trade_date": blk.trade_date.values})

        def R(s_, w, fn, mp=None):
            return getattr(pd.Series(s_).rolling(w, min_periods=mp or max(2, int(w * 0.8))), fn)().astype(f32)

        for w in (5, 10, 20):
            on_m = R(on, w, "mean"); id_m = R(idr, w, "mean")
            d[f"on_mean{w}"] = on_m
            d[f"id_mean{w}"] = id_m
            d[f"on_minus_id{w}"] = (on_m.astype(float) - id_m.astype(float)).astype(f32)
            tot = (on_m.abs().astype(float) + id_m.abs().astype(float))
            d[f"on_share{w}"] = (on_m.astype(float) / tot.replace(0, np.nan)).astype(f32)
        d["on_vol20"] = R(on, 20, "std")
        d["id_vol20"] = R(idr, 20, "std")
        d["on_id_vol_ratio20"] = (d["on_vol20"].astype(float) /
                                  d["id_vol20"].astype(float).replace(0, np.nan)).astype(f32)
        d["on_skew20"] = R(on, 20, "skew")
        d["on_negfreq20"] = R((on < 0).astype(f32), 20, "mean")

        # 非对称 / 特质崩盘风险族（L-093 预筛里 downvol20 全空的修复）
        vol20 = R(r, 20, "std")
        d["vol20_rf"] = vol20
        neg = np.minimum(rf, 0.0)
        d["ddev20"] = (pd.Series(np.square(neg)).rolling(20, min_periods=12).mean().pow(0.5)).astype(f32)
        d["ddev_ratio20"] = (d["ddev20"].astype(float) / vol20.astype(float).replace(0, np.nan)).astype(f32)
        d["dvol20"] = pd.Series(rf).where(rf < 0).rolling(20, min_periods=6).std().astype(f32)
        d["dvol_ratio20"] = (d["dvol20"].astype(float) / vol20.astype(float).replace(0, np.nan)).astype(f32)
        d["skew20"] = R(r, 20, "skew")
        d["kurt20"] = R(r, 20, "kurt")
        d["max1_20"] = R(r, 20, "max")
        d["min1_20"] = R(r, 20, "min")
        d["range_maxmin20"] = (d["max1_20"].astype(float) - d["min1_20"].astype(float)).astype(f32)

        cov = pd.Series(rf).rolling(20, min_periods=16).cov(pd.Series(m))
        mvar = pd.Series(m).rolling(20, min_periods=16).var()
        beta = (cov / mvar.where(mvar > 0)).astype(f32)
        d["beta20"] = beta
        resid = (rf - beta.astype(float) * m)
        d["ivol20_rf"] = pd.Series(resid).rolling(20, min_periods=16).std().astype(f32)
        d["rskew20"] = pd.Series(resid).rolling(20, min_periods=16).skew().astype(f32)
        d["rmax1_20"] = pd.Series(resid).rolling(20, min_periods=16).max().astype(f32)
        d["rmin1_20"] = pd.Series(resid).rolling(20, min_periods=16).min().astype(f32)
        rneg = np.minimum(resid.values, 0.0)
        d["rddev20"] = (pd.Series(np.square(rneg)).rolling(20, min_periods=12).mean().pow(0.5)).astype(f32)
        d["rddev_ratio20"] = (d["rddev20"].astype(float) /
                              d["ivol20_rf"].astype(float).replace(0, np.nan)).astype(f32)
        # downside beta：只用市场下跌日
        mdn = pd.Series(m).where(pd.Series(m) < 0)
        cov_d = pd.Series(rf).rolling(20, min_periods=6).cov(mdn)
        mvar_d = mdn.rolling(20, min_periods=6).var()
        d["dbeta20"] = (cov_d / mvar_d.where(mvar_d > 0)).astype(f32)
        d["dbeta_ratio20"] = (d["dbeta20"].astype(float) / beta.astype(float).replace(0, np.nan)).astype(f32)

        # 存量指标残列（部分做尺度归一）
        for c in IND_COLS:
            v = blk[c].astype(f32)
            d[c] = v.values
        d["bbi_rel"] = (cl.astype(float) / blk.bbi.astype(float).replace(0, np.nan) - 1.0).astype(f32)
        d["ema12_rel"] = (cl.astype(float) / blk.ema_12.astype(float).replace(0, np.nan) - 1.0).astype(f32)
        d["ema50_rel"] = (cl.astype(float) / blk.ema_50.astype(float).replace(0, np.nan) - 1.0).astype(f32)
        d["atr_n"] = (blk.atr.astype(float) / cl.astype(float).replace(0, np.nan)).astype(f32)
        d["boll_pos"] = ((cl.astype(float) - blk.boll_lower.astype(float)) /
                         (blk.boll_upper.astype(float) - blk.boll_lower.astype(float)).replace(0, np.nan)).astype(f32)
        d["obv_slope20"] = (pd.Series(blk.obv.astype(float)).pct_change(20)).astype(f32)
        d["mv5_rel"] = (blk.ma_volume_5.astype(float) / blk.ma_volume_10.astype(float).replace(0, np.nan)).astype(f32)
        d["chip_spread"] = ((blk.chip_cost_85.astype(float) - blk.chip_cost_15.astype(float)) /
                            blk.chip_cost_50.astype(float).replace(0, np.nan)).astype(f32)
        d["chip_pos"] = (cl.astype(float) / blk.chip_weight_avg.astype(float).replace(0, np.nan) - 1.0).astype(f32)

        entry = o.shift(-1)
        d["entry"] = entry.astype(f32)
        for hh in (3, 5):
            d[f"fwd{hh}"] = ((cl.shift(-hh) / entry - 1.0) * 100.0).astype(f32)
        parts.append(d[d.trade_date >= KEEP_FROM])

    out = pd.concat(parts, ignore_index=True)
    out = out[out.entry > 0]
    out.to_pickle(PANEL)
    print(f"saved {PANEL} rows={len(out):,} cols={out.shape[1]} "
          f"mem={out.memory_usage(deep=True).sum() / 1e9:.2f}GB")
    return out


if __name__ == "__main__":
    build_panel()
