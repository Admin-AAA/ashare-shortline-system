"""[§1057 评估] §282 机械判定 + 超额 + 抓涨停命中率汇总。

用法：cd backend/scripts/midterm
  PYTHONPATH=../..:.:../shortline_research ../../.venv/bin/python ../shortline_research/lu_eval_s1057.py
先调 saf_s987_eval.py（§282 判定/质量指标），再补 cohort_bench 超额与 lu_hit 汇总。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import polars as pl

from lt_research6_ctrl import cohort_bench

c = pl.col
D = os.path.join(os.path.dirname(__file__), "..", "..", "output", "research", "s1057")
PANEL = os.path.join(os.path.dirname(__file__), "..", "..", "output", "research", "lu_events", "panel.parquet")
NAMES = ["FBC_E1", "DBF1_E1", "DBF2_E1", "NMB_E1"]

if __name__ == "__main__":
    res = os.path.join(D, "res.json")
    subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "saf_s987_eval.py"), res, *NAMES], check=True)
    allres = json.load(open(res))
    panel = pl.read_parquet(PANEL)
    extra = {}
    for name in NAMES:
        extra[name] = {}
        for tag in ("R12", "N3"):
            f = os.path.join(D, f"trades_{name}_{tag}.csv")
            if not os.path.exists(f):
                continue
            t = pl.read_csv(f, schema_overrides={"code": pl.Utf8})
            b = cohort_bench(panel, t.select("code", "entry", "exit"))
            bench = round(100 * float(b["bench"].mean()), 2)
            avg = allres.get(name, {}).get({"R12": "R12 2025-10~2026-10", "N3": "N3 2024-01~2026-10"}[tag], {}).get("avg")
            extra[name][tag] = {"bench": bench, "excess": None if avg is None else round(avg - bench, 2)}
        # 抓涨停命中率（res.json 已存 _lu_hit）
        hits = {ph: v.get(name) for ph, v in allres.get("_lu_hit", {}).items() if v.get(name)}
        extra[name]["lu_hit"] = hits
    json.dump(extra, open(os.path.join(D, "extra.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps(extra, indent=1, ensure_ascii=False))