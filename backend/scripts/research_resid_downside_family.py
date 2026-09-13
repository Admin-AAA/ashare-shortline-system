"""特质风险族【下行/不对称】衍生因子全市场三窗口 strictly-prior 预筛（只读，不改生产）。

动机（与 L-093 的显式切割）：
- L-093 证明了「残差特质波动 ivol20 的 强牛 × top20% 尾部 + 真实递补」硬否决三窗 Δcohort 全正。
  其机制自述为「剔除市场 beta 后特质风险最高的一档次日崩塌/跳空止损概率最高」——即**左尾**风险。
- 但 L-093 用的 ivol20 是**双侧**标准差，把右尾上行波动也算进风险。生产出场是非对称的
  （+6% 止盈截断赢家 / -4.5% 止损 + 隔夜跳空放大输家），理论上真正驱动亏损的是**下行侧**
  的特质波动。故提出可证伪假设 H-A：**残差下行半波动（ivol_dn20）的强牛 top 尾部比双侧
  ivol20 更毒、且在对 ivol20 正交化后仍有增量**。
- L-085~L-092 已证伪的是【存量列 + 不分 regime 的线性/单调关联 + 整日择时门控】与
  【L-093 同族的"水平" carrier（总波动 vol20 / MAX1 / 偏度 / 60 日动量）】，那批是**原始**
  收益矩、非**残差**矩；本脚本构造的是残差分布的下行矩，属未被构造过的维度。
- 顺带记录：旧 `research_anomaly_factors.py` 的 `downvol20`（原始下行波动）用
  `r.rolling(20, min_periods=16).std()` 作用在 `r.where(r<0)` 上，窗口内 20 日只有约 10 个
  非空 → 恒 NaN，**从未真正被检验过**（三窗全列为 '-'）。本条一并补上原始下行波动对照。

因子定义（全部 T 收盘可得，严格无前视）：
- r_t = pct_chg/100, m_t = 中证500(000905.SH) pct_chg/100
- beta20 = Cov(r,m)_20 / Var(m)_20 (min_periods 16)；resid_t = r_t - beta20_t * m_t
- ivol20      = std(resid, 20)                       (双侧，L-093 现行载体)
- ivol_dn20   = sqrt(mean(min(resid,0)^2, 20))       (下行半波动，主假设)
- ivol_up20   = sqrt(mean(max(resid,0)^2, 20))       (上行半波动，对照)
- ivol_asym20 = (ivol_dn20 - ivol_up20) / ivol20     (残差左右不对称)
- dn_ratio20  = ivol_dn20 / ivol20                   (下行占比)
- worst_resid20 = min(resid, 20)                     (尾部深度)
- beta_dn20   = sum(r*m | m<0)/sum(m^2 | m<0) 20日窗口内下行 beta（窗内下行日 >=5）
- beta_asym20 = beta_dn20 - beta_up20
- rdnvol20    = sqrt(mean(min(r,0)^2, 20))           (原始下行波动，补旧脚本缺口)
- rdn_ratio20 = rdnvol20 / std(r,20)

用法：
  python research_resid_downside_family.py build    # 构建面板 -> resid_downside_panel.pkl
  python research_resid_downside_family.py screen   # 三窗口强牛条件化分诊
"""
from __future__ import annotations
import sys, json, warnings
from pathlib import Path
import numpy as np
import pandas as pd
from sqlalchemy import text

warnings.filterwarnings("ignore")
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))
from app.db.session import SessionLocal  # noqa: E402

OUT = B / "output" / "research"
OUT.mkdir(parents=True, exist_ok=True)
PANEL = OUT / "resid_downside_panel.pkl"
SNAP = OUT / "resid_downside_regime.json"

WINDOWS = {
    "W1_frozen": ("2025-01-15", "2026-01-16"),
    "W2_forward": ("2026-02-09", "2026-08-05"),
    "W3_third": ("2026-08-07", "2026-09-02"),
}
LOAD_FROM = "2024-06-01"
KEEP_FROM = "2025-01-06"
W = 20
MP = 16


def build_panel() -> pd.DataFrame:
    with SessionLocal() as s:
        k = pd.DataFrame(s.execute(text(
            """SELECT code, trade_date, open, close, pct_chg, amount
               FROM daily_kbars
               WHERE trade_date >= :s AND close > 0 AND open > 0
                 AND code NOT LIKE '8%' AND code NOT LIKE '4%' AND code NOT LIKE '920%'
                 AND code NOT IN (SELECT code FROM stocks WHERE name LIKE '%ST%')"""),
            {"s": LOAD_FROM}).mappings().all())
        idx = pd.DataFrame(s.execute(text(
            "SELECT trade_date, pct_chg FROM index_daily WHERE ts_code='000905.SH'")
        ).mappings().all())
    for c in ("open", "close", "pct_chg", "amount"):
        k[c] = k[c].astype(np.float32)
    mkt = idx.assign(mkt=(idx.pct_chg.astype(float) / 100).astype(np.float32))[["trade_date", "mkt"]]
    k = k.merge(mkt, on="trade_date", how="left").sort_values(["code", "trade_date"]).reset_index(drop=True)

    parts = []
    for code, blk in k.groupby("code", sort=False):
        blk = blk.reset_index(drop=True)
        cl = blk.close
        r = (blk.pct_chg / 100.0).astype(np.float64)
        m = blk.mkt.astype(np.float64)
        if m.isna().all():
            continue
        cov = r.rolling(W, min_periods=MP).cov(m)
        mvar = m.rolling(W, min_periods=MP).var()
        beta = cov / mvar.where(mvar > 0)
        resid = r - beta * m

        def roll_mean(s, w=W, mp=MP):
            return s.rolling(w, min_periods=mp).mean()

        d = pd.DataFrame({"code": code, "trade_date": blk.trade_date.values})
        d["beta20"] = beta
        d["ivol20"] = np.sqrt((resid ** 2).rolling(W, min_periods=MP).mean())
        d["ivol_dn20"] = np.sqrt(roll_mean(resid.clip(upper=0.0) ** 2))
        d["ivol_up20"] = np.sqrt(roll_mean(resid.clip(lower=0.0) ** 2))
        d["worst_resid20"] = resid.rolling(W, min_periods=MP).min()
        worse_share = (resid < 0).astype(np.float64).where(resid.notna())
        d["dn_share20"] = worse_share.rolling(W, min_periods=MP).mean()
        # 下行 / 上行 beta（窗内 m<0 / m>0 的过原点 OLS）
        mdn = (m < 0) & m.notna()
        mup = (m > 0) & m.notna()
        for tag, mask in (("dn", mdn), ("up", mup)):
            num = (r * m).where(mask, 0.0)
            den = (m ** 2).where(mask, 0.0)
            cnt = mask.astype(np.float64)
            nsum = num.rolling(W, min_periods=1).sum()
            dsum = den.rolling(W, min_periods=1).sum()
            csum = cnt.rolling(W, min_periods=1).sum()
            b = (nsum / dsum.where(dsum > 0)).where(csum >= 5)
            d[f"beta_{tag}20"] = b
        d["rdnvol20"] = np.sqrt(roll_mean(r.clip(upper=0.0) ** 2))
        d["rvol20"] = r.rolling(W, min_periods=MP).std()
        # 动量/总量对照（用于排除"换名重挖动量/低波"的可能）
        d["ret20"] = cl.pct_change(20)
        d["ret60"] = cl.pct_change(60)
        d["resid_mean20"] = resid.rolling(W, min_periods=MP).mean()
        d["close"] = cl.values

        # 前瞻：T+1 open 入场 -> T+h close
        entry = cl.shift(-1)
        d["entry"] = entry.astype(np.float32)
        for hh in (2, 3, 5):
            d[f"fwd{hh}"] = ((cl.shift(-hh) / entry - 1.0) * 100.0).astype(np.float32)
        d["amt_yi"] = (blk.amount / 1e5).astype(np.float32)
        parts.append(d)
    out = pd.concat(parts, ignore_index=True)
    out = out[(out.trade_date >= KEEP_FROM) & (out.entry > 0)]
    out["ivol_asym20"] = ((out.ivol_dn20 - out.ivol_up20) / out.ivol20).astype(np.float32)
    out["dn_ratio20"] = (out.ivol_dn20 / out.ivol20).astype(np.float32)
    out["rdn_ratio20"] = (out.rdnvol20 / out.rvol20).astype(np.float32)
    out["beta_asym20"] = (out.beta_dn20 - out.beta_up20).astype(np.float32)
    f32 = [c for c in out.columns if c not in ("code", "trade_date")]
    out[f32] = out[f32].astype(np.float32)
    out.to_pickle(PANEL)
    print(f"saved {PANEL} rows={len(out):,} dates {out.trade_date.min()}..{out.trade_date.max()} "
          f"mem={out.memory_usage(deep=True).sum()/1e9:.2f}GB")
    return out


def regime_labels() -> dict[str, str]:
    """用生产同一函数重建每日大盘状态（强牛/偏多/震荡/弱势）。"""
    from scripts.research_forward_validation import build_market_snapshots
    from app.engine.aggregator import _market_base
    snap = build_market_snapshots(None)
    lab = {d: _market_base(s) for d, s in snap.items()}
    SNAP.write_text(json.dumps(lab, ensure_ascii=False))
    return lab


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "build"
    if mode == "build":
        build_panel()
    elif mode == "regime":
        lab = regime_labels()
        print("regime labels", len(lab))
