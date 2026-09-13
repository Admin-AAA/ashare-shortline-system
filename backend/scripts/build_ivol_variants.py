"""构建 ivol 参数变体面板，检验结论是否依赖精确构造（20日/中证500）。
变体：ivol20_300(对沪深300)、ivol30/ivol40(对中证500)、ivol20_ew(对等权全市场)。
全部 strictly-prior（T 收盘已知）。输出 ivol_variants.pkl。"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import text

B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.db.session import SessionLocal
OUT = B / "output" / "research"


def main():
    with SessionLocal() as s:
        k = pd.DataFrame(s.execute(text(
            """SELECT code, trade_date, close, pct_chg FROM daily_kbars
               WHERE trade_date>='2024-06-01' AND close>0
                 AND code NOT LIKE '8%' AND code NOT LIKE '4%' AND code NOT LIKE '920%'
                 AND code NOT IN (SELECT code FROM stocks WHERE name LIKE '%ST%')"""), {}).mappings().all())
        idx = pd.DataFrame(s.execute(text(
            "SELECT ts_code, trade_date, pct_chg FROM index_daily WHERE ts_code IN ('000905.SH','000300.SH')")).mappings().all())
    k["pct_chg"] = k.pct_chg.astype(np.float32)
    k = k.sort_values(["code", "trade_date"]).reset_index(drop=True)
    # 等权全市场日收益
    ew = k.groupby("trade_date").pct_chg.mean().rename("ew").reset_index()
    i500 = idx[idx.ts_code == "000905.SH"][["trade_date", "pct_chg"]].rename(columns={"pct_chg": "m500"})
    i300 = idx[idx.ts_code == "000300.SH"][["trade_date", "pct_chg"]].rename(columns={"pct_chg": "m300"})
    k = k.merge(i500, on="trade_date", how="left").merge(i300, on="trade_date", how="left").merge(ew, on="trade_date", how="left")
    for c in ["m500", "m300", "ew"]:
        k[c] = (k[c].astype(float) / 100.0).astype(np.float32)

    def ivol_series(r, m, w):
        cov = r.rolling(w, min_periods=int(w * 0.8)).cov(m)
        mvar = m.rolling(w, min_periods=int(w * 0.8)).var()
        beta = cov / mvar.where(mvar > 0)
        resid = r - beta * m
        return resid.rolling(w, min_periods=int(w * 0.8)).std().astype(np.float32)

    parts = []
    for code, blk in k.groupby("code", sort=False):
        blk = blk.reset_index(drop=True)
        r = (blk.pct_chg / 100.0).astype(np.float32)
        d = pd.DataFrame({"trade_date": blk.trade_date.values, "code": code})
        d["ivol20_500"] = ivol_series(r, blk.m500, 20)
        d["ivol20_300"] = ivol_series(r, blk.m300, 20)
        d["ivol30_500"] = ivol_series(r, blk.m500, 30)
        d["ivol40_500"] = ivol_series(r, blk.m500, 40)
        d["ivol20_ew"] = ivol_series(r, blk.ew, 20)
        parts.append(d[d.trade_date >= "2025-01-06"])
    out = pd.concat(parts, ignore_index=True)
    out.to_pickle(OUT / "ivol_variants.pkl")
    print("saved ivol_variants.pkl", out.shape, out.columns.tolist())


if __name__ == "__main__":
    main()
