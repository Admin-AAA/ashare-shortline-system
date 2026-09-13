"""L-081 选中池剩余日线特征三窗扫描（只读探索，手写 cohort 仅用于预筛）。
把 daily_kbars 预计算指标 + 少量 OHLC 衍生特征，在三窗 v2 选中票上算 Spearman IC、
三分位净收益与单调性；只把【三窗 IC 同号 + 三窗三分位同向单调 + 非单日聚集】的特征
升级到 canonical 配对检验。预期：日线横截面在选中池无三窗稳定信息（L-061/065/073）。
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import create_engine, text
from scipy.stats import spearmanr

B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
OUT = B / "output" / "research"
FEATS = ["ivol20", "atr_n", "bias_5", "bias_10", "bias_20", "wr_6", "wr_10", "rsi_6",
         "rsi_12", "rsi_24", "max_dd20", "chip_winner_rate", "upper_shadow_count20",
         "vol_ratio_5", "vol_ratio_20", "turnover_rate", "gap", "clr", "range1", "ret5", "vol20"]


def _db():
    for line in open(B / ".env"):
        if line.startswith("ASHARE_DATABASE_URL="):
            return create_engine(line.split("=", 1)[1].strip().strip('"').strip("'"))
    raise RuntimeError


def load_panel(codes, d0, d1):
    q = text("""SELECT code, trade_date dt, open, high, low, close, pct_chg, turnover_rate,
                      ivol20, atr, bias_5, bias_10, bias_20, wr_6, wr_10, rsi_6, rsi_12, rsi_24,
                      max_dd20, chip_winner_rate, upper_shadow_count20, vol_ratio_5, vol_ratio_20
               FROM daily_kbars WHERE code IN :codes AND trade_date BETWEEN :a AND :b""")
    codes = tuple(codes)
    with _db().connect() as c:
        df = pd.DataFrame(c.execute(q, {"codes": codes, "a": d0, "b": d1}).mappings().all())
    df["dt"] = df.dt.astype(str); df["code"] = df.code.astype(str)
    df = df.sort_values(["code", "dt"])
    g = df.groupby("code")
    df["prevclose"] = g.close.shift(1); df["prev5"] = g.close.shift(5)
    df["gap"] = df.open / df.prevclose - 1
    df["clr"] = (df.close - df.low) / (df.high - df.low).replace(0, np.nan)
    df["range1"] = (df.high - df.low) / df.close
    df["ret5"] = df.close / df.prev5 - 1
    df["atr_n"] = df.atr / df.close
    df["vol20"] = g.pct_chg.transform(lambda x: x.rolling(20, 10).std())
    return df


def screen_window(name, sel, panel):
    m = sel.merge(panel, on=["dt", "code"], how="left")
    rows = []
    for f in FEATS:
        d = m.dropna(subset=[f, "net"])
        if len(d) < 60 or d[f].nunique() < 5:
            rows.append((f, len(d), np.nan, None)); continue
        ic = spearmanr(d[f], d.net).statistic
        try:
            d = d.copy(); d["q"] = pd.qcut(d[f].rank(method="first"), 3, labels=["Q1", "Q2", "Q3"])
            q = d.groupby("q", observed=True).net.mean()
            mono_up = q.Q1 < q.Q2 < q.Q3; mono_dn = q.Q1 > q.Q2 > q.Q3
            nd = d.groupby("q", observed=True).dt.nunique().min()
            rows.append((f, len(d), ic, (round(q.Q1, 3), round(q.Q2, 3), round(q.Q3, 3),
                                         "UP" if mono_up else "DN" if mono_dn else ".", int(nd))))
        except Exception:
            rows.append((f, len(d), ic, None))
    return m, rows


def main():
    fz = pd.read_pickle(OUT / "canon_frozen_v2_selected.pkl")[["dt", "code", "net"]]
    fw = pd.read_pickle(OUT / "canon_forward_v2_selected.pkl")[["dt", "code", "net"]]
    r3 = pd.read_pickle(OUT / "run3_v2_selected.pkl")[["dt", "code", "net"]]
    for d in (fz, fw, r3):
        d["dt"] = d.dt.astype(str); d["code"] = d.code.astype(str)
    codes = set(fz.code) | set(fw.code) | set(r3.code)
    panel = load_panel(codes, "2024-12-01", "2026-09-11")
    panel.to_pickle(OUT / "sel_daily_feature_panel.pkl")
    res = {}
    for name, sel in [("FROZEN", fz), ("FORWARD", fw), ("RUN3", r3)]:
        _, rows = screen_window(name, sel, panel)
        res[name] = {r[0]: (r[2], r[3]) for r in rows}
        print(f"\n===== {name} =====")
        for f, n, ic, q in rows:
            print(f"  {f:22s} n={n:4d} IC={ic:+.4f} 三分位={q}")
    # 汇总：三窗 IC 同号 & 三分位同向
    print("\n===== 三窗一致性筛选 =====")
    for f in FEATS:
        ics = [res[w][f][0] for w in ("FROZEN", "FORWARD", "RUN3")]
        qs = [res[w][f][1] for w in ("FROZEN", "FORWARD", "RUN3")]
        if any(np.isnan(x) for x in ics): continue
        same_sign = all(x > 0 for x in ics) or all(x < 0 for x in ics)
        dirs = [q[3] if q else "." for q in qs]
        mono_same = dirs.count("UP") == 3 or dirs.count("DN") == 3
        flag = "★候选" if (same_sign and mono_same) else ""
        print(f"  {f:22s} IC {ics[0]:+.3f}/{ics[1]:+.3f}/{ics[2]:+.3f} 单调 {dirs} {flag}")


if __name__ == "__main__":
    main()
