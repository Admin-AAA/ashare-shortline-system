"""[§1058 评估] §282 判定 + OR vs REF 重叠 + QVIX-only 日增量归因 + 超额。

用法：cd backend/scripts/midterm
  set -a && . ../../.env && set +a
  PYTHONPATH=../..:.:../shortline_research ../../.venv/bin/python ../shortline_research/qvix_eval_s1058.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import polars as pl

from lt_research6_ctrl import cohort_bench

c = pl.col
D = os.path.join(os.path.dirname(__file__), "..", "..", "output", "research", "s1058")
PANEL = os.path.join(D, "panel_qvix.parquet")
NAMES = ["REF_CAP", "QV2_ONLY", "QVC_OR"]
R12, N3 = "R12 2025-10~2026-10", "N3 2024-01~2026-10"


def sig_day_map(df: pl.DataFrame) -> dict:
    """entry(买入日) → 信号日（市场日历上前一交易日）。"""
    cal = sorted(df["trade_date"].unique().to_list())
    prev = {cal[i]: cal[i - 1] for i in range(1, len(cal))}
    return prev


if __name__ == "__main__":
    res = os.path.join(D, "res.json")
    subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "saf_s987_eval.py"), res, *NAMES], check=True)
    allres = json.load(open(res))
    df = pl.read_parquet(PANEL)
    # 重算触发器（panel 里只有原始 qvix/lu_ld，rsi6 需补）
    from mrsi_s1013 import market_rsi
    df = df.join(market_rsi(df).select("trade_date", "rsi6"), on="trade_date", how="left")
    df = df.with_columns(
        ((c("rsi6") < 20) & (c("mkt_lu_ld") <= 0.34)).fill_null(False).alias("PRICE_CAP"),
        (c("qv_chg5") > 0.25).fill_null(False).alias("QV_SPIKE"),
    )
    # 日级触发器
    day = (df.select("trade_date", "PRICE_CAP", "QV_SPIKE").unique().sort("trade_date")
           .with_columns((c("QV_SPIKE") & ~c("PRICE_CAP")).alias("QV_ONLY")))
    prev = sig_day_map(df)
    extra = {}
    for name in NAMES:
        extra[name] = {}
        for tag, ph in (("R12", R12), ("N3", N3)):
            f = os.path.join(D, f"trades_{name}_{tag}.csv")
            if not os.path.exists(f):
                continue
            t = pl.read_csv(f, schema_overrides={"code": pl.Utf8})
            b = cohort_bench(df, t.select("code", "entry", "exit"))
            avg = allres.get(name, {}).get(ph, {}).get("avg")
            bench = round(100 * float(b["bench"].mean()), 2)
            extra[name][tag] = {"bench": bench, "excess": None if avg is None else round(avg - bench, 2)}
    # OR vs REF 重叠（N3，code+entry）
    fo, fr = os.path.join(D, "trades_QVC_OR_N3.csv"), os.path.join(D, "trades_REF_CAP_N3.csv")
    if os.path.exists(fo) and os.path.exists(fr):
        to = pl.read_csv(fo, schema_overrides={"code": pl.Utf8})
        tr = pl.read_csv(fr, schema_overrides={"code": pl.Utf8})
        ko = set(zip(to["code"].to_list(), to["entry"].to_list()))
        kr = set(zip(tr["code"].to_list(), tr["entry"].to_list()))
        inter = ko & kr
        extra["overlap_N3"] = {"or_n": len(ko), "ref_n": len(kr), "inter": len(inter),
                               "inter_pct_of_ref": round(100 * len(inter) / max(1, len(kr)), 1),
                               "or_extra": len(ko - kr)}
        # QVIX-only 日 vs PRICE_CAP 日归因（OR 的 N3 逐笔按信号日类型切）
        t = to.with_columns(c("entry").map_elements(lambda e: prev.get(e, ""), return_dtype=pl.Utf8).alias("sig"))
        t = t.join(day.select("trade_date", "PRICE_CAP", "QV_ONLY"), left_on="sig", right_on="trade_date", how="left")
        for lab, mask in (("price_cap_days", c("PRICE_CAP") == True), ("qv_only_days", c("QV_ONLY") == True)):
            w = t.filter(mask)
            if w.height:
                r = w["ret"].to_numpy()
                extra.setdefault("or_attribution_N3", {})[lab] = {
                    "n": int(w.height), "avg": round(100 * float(r.mean()), 2),
                    "win": round(100 * float((r > 0).mean()), 1),
                    "pf": round(float(r[r > 0].sum() / max(1e-9, -r[r < 0].sum())), 2)}
    json.dump(extra, open(os.path.join(D, "extra.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps(extra, indent=1, ensure_ascii=False))