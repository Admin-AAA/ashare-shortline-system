"""[§1059–§1062 评估] §282 判定（saf_s987_eval）+ 与现役策略同日同票重叠（L-221 a）+ 日期重叠 + 互相重叠。

现役出票来源（只读）：
  中长线：output/strategy_health/rolling_r12/<最新月>/picks/*.json（现行生产选股代码重放，origin 以文件为准），
          只取 rolling_r12_latest.json 的 midlong 列表里的现役 strategy_id。
  短线：MySQL recommendations ⨝ recommendation_runs（status=completed），skill_breakdown_json 里的 strategy_id，
        只取 rolling_r12_latest.json 的 short 列表；只 SELECT，不写库。
用法：cd backend/scripts/midterm
  set -a && . <主工作树>/backend/.env && set +a
  PYTHONPATH=../..:.:../shortline_research ../../.venv/bin/python ../shortline_research/pan_s1059_eval.py
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys

import polars as pl

c = pl.col
B = os.path.join(os.path.dirname(__file__), "..", "..")
D = os.path.join(B, "output", "research", "s1059")
PANEL = os.path.join(B, "output", "research", "s1058", "panel_qvix.parquet")
NAMES = ["RPDROP_h5", "SMALL_h5", "VALUE_h5", "RPLAN_h5", "REF_LIQ"]
NEW = NAMES[:4]
R12_S, R12_E = "2025-10-01", "2026-10-16"


def trades(name: str, tag: str) -> pl.DataFrame | None:
    f = os.path.join(D, f"trades_{name}_{tag}.csv")
    return pl.read_csv(f, schema_overrides={"code": pl.Utf8}) if os.path.exists(f) else None


def with_sig(t: pl.DataFrame, prev: dict) -> pl.DataFrame:
    return t.with_columns(c("entry").map_elements(lambda e: prev.get(e, ""), return_dtype=pl.Utf8).alias("sig"))


def live_mid() -> pl.DataFrame:
    latest = json.load(open(os.path.join(B, "output", "strategy_health", "rolling_r12_latest.json")))
    sids = set(latest.get("midlong", {}))
    month = sorted(os.listdir(os.path.join(B, "output", "strategy_health", "rolling_r12")))[-1]
    rows = []
    for f in glob.glob(os.path.join(B, "output", "strategy_health", "rolling_r12", month, "picks", "*.json")):
        b = json.load(open(f))
        if b.get("strategy_id") not in sids:
            continue
        for p in b.get("picks", []):
            rows.append({"sid": b["strategy_id"], "code": str(p.get("code", "")).zfill(6), "sig": b["signal_date"]})
    return pl.DataFrame(rows, schema={"sid": pl.Utf8, "code": pl.Utf8, "sig": pl.Utf8}, orient="row")


def live_short() -> pl.DataFrame:
    from sqlalchemy import text

    from app.db.session import SessionLocal

    latest = json.load(open(os.path.join(B, "output", "strategy_health", "rolling_r12_latest.json")))
    sids = sorted(latest.get("short", {}).get("strategies", {}))
    db = SessionLocal()
    try:
        rs = db.execute(text(
            """
            SELECT DISTINCT jt.strategy_id, rec.code, rec.trade_date
            FROM recommendations rec
            JOIN recommendation_runs u ON rec.run_id = u.run_id
            JOIN JSON_TABLE(rec.skill_breakdown_json, '$[*]'
                 COLUMNS (strategy_id VARCHAR(80) PATH '$.strategy_id')) jt
            WHERE u.status = 'completed' AND rec.trade_date BETWEEN :s AND :e
            """), {"s": R12_S, "e": R12_E}).fetchall()
    finally:
        db.close()
    rows = [{"sid": str(r[0]), "code": str(r[1]).zfill(6), "sig": str(r[2])[:10]} for r in rs if str(r[0]) in sids]
    return pl.DataFrame(rows, schema={"sid": pl.Utf8, "code": pl.Utf8, "sig": pl.Utf8}, orient="row")


if __name__ == "__main__":
    res = os.path.join(D, "res.json")
    subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "saf_s987_eval.py"), res, *NAMES], check=True)
    cal = sorted(pl.read_parquet(PANEL, columns=["trade_date"])["trade_date"].unique().to_list())
    prev = {cal[i]: cal[i - 1] for i in range(1, len(cal))}
    mid, short = live_mid(), live_short()
    live = pl.concat([mid, short]).unique()
    out = {"live_rows": {"mid_long": mid.height, "short": short.height,
                         "short_by_sid": {k: int(v) for k, v in short.group_by("sid").len().iter_rows()}}}
    # 现役 rsi6 恐慌日（日期级）
    trig_days = set(short.filter(c("sid") == "rsi6-capitulation")["sig"].to_list())
    for name in NEW:
        t = trades(name, "R12")
        if t is None:
            continue
        t = with_sig(t, prev).select("code", "sig").unique()
        j = t.join(live, on=["code", "sig"], how="inner")
        hit = j.select("code", "sig").unique().height
        out[name] = {
            "R12_trades": t.height,
            "codedate_overlap": hit,
            "codedate_overlap_pct": round(100 * hit / max(1, t.height), 1),
            "by_sid": {k: int(v) for k, v in j.group_by("sid").len().iter_rows()},
            "sigdays": t["sig"].n_unique(),
            "date_overlap_with_rsi6_pct": round(100 * len(set(t["sig"].to_list()) & trig_days) / max(1, t["sig"].n_unique()), 1),
        }
    # 与 REF_LIQ、彼此之间（N3 同日同票）
    n3 = {n: trades(n, "N3") for n in NAMES}
    keys = {n: set(zip(x["code"].to_list(), x["entry"].to_list())) for n, x in n3.items() if x is not None}
    pair = {}
    for i, a in enumerate(NAMES):
        for b in NAMES[i + 1:]:
            if a in keys and b in keys:
                inter = len(keys[a] & keys[b])
                pair[f"{a}~{b}"] = {"inter": inter, "pct_of_min": round(100 * inter / max(1, min(len(keys[a]), len(keys[b]))), 1)}
    out["pairwise_N3"] = pair
    # 同市值五分位超额（只展示；harness 内 cohort_bench 在裁剪后的面板上取不到，改在全面板上算）
    from lt_research6_ctrl import cohort_bench

    allres = json.load(open(res))
    pan = pl.read_parquet(PANEL, columns=["code", "trade_date", "liq", "total_mv", "ao"])
    for name in NAMES:
        for tag, ph in (("R12", "R12 2025-10~2026-10"), ("N3", "N3 2024-01~2026-10")):
            t = trades(name, tag)
            if t is None:
                continue
            bench = round(100 * float(cohort_bench(pan, t.select("code", "entry", "exit"))["bench"].mean()), 2)
            avg = allres.get(name, {}).get(ph, {}).get("avg")
            out.setdefault("excess", {}).setdefault(name, {})[tag] = {
                "bench": bench, "excess": None if avg is None else round(avg - bench, 2)}
    json.dump(out, open(os.path.join(D, "overlap.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps(out, indent=1, ensure_ascii=False))
