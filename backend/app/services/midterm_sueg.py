"""[中线] §489 SUEG_h20：标准化意外盈利 ∩ 业绩加速（mid-sue-accel）实盘候选。

研究：scripts/midterm/sueg_s489.py（lt_research122 SUE ∩ G，排序 min(SUE,20)）。
  SUE：单季扣非 UE/σ（过去 8 期 UE 的滚动标准差，min_samples=6），SUE≥2 且单季扣非 q>0。
  G：现役业绩加速（扣非同比≥50、营收同比≥20、扣非>0、同比加速）。
事件时点 / 股池：同 earnings_accel_candidates（公告日落在 (上一交易日, as_of]，liq 同 _liq_ok）。
数据：G 与现役 EA 相同（fina_indicator_vip 最近 3 期）；SUE 历史用本地 data/midterm/fina/<code>.parquet，
  当期 profit_dedt 以网关公告行为准（缓存缺值时补齐），不另改数据网关。
实盘推荐不设仓位上限（k_slots/max_new 仅回测口径）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
FINA_DIR = ROOT / "data" / "midterm" / "fina"
_ENDS = ("0331", "0630", "0930", "1231")


def _qi(end: str) -> int:
    # 与 lt_research122 一致：year*4 + month//3（0331→1 … 1231→4）
    return int(end[:4]) * 4 + int(end[4:6]) // 3


def _sue_for(code: str, end_date: str, profit_dedt_cur: float | None) -> tuple[float | None, float | None]:
    """按 lt_research122 口径算 (sue, q)；数据不足返回 (None, None)。"""
    path = FINA_DIR / f"{code}.parquet"
    rows: list[dict[str, Any]] = []
    if path.exists():
        try:
            df = pd.read_parquet(path, columns=["ann_date", "end_date", "profit_dedt"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("sueg read %s: %s", path.name, exc)
            df = None
        if df is not None and not df.empty:
            df = df.copy()
            df["ann_date"] = df["ann_date"].astype(str).str.strip()
            df["end_date"] = df["end_date"].astype(str).str.strip()
            df["profit_dedt"] = pd.to_numeric(df["profit_dedt"], errors="coerce")
            df = df[df["end_date"].str.slice(4, 8).isin(_ENDS)]
            df = df.dropna(subset=["ann_date", "end_date"])
            # 同 end_date 取最早公告；同日多行优先非空 profit_dedt（对齐 polars unique 实得）
            df = df.sort_values(["ann_date", "profit_dedt"], ascending=[True, False], na_position="last")
            df = df.drop_duplicates("end_date", keep="first")
            rows = df.to_dict("records")
    by_end = {str(r["end_date"]): r for r in rows}
    if profit_dedt_cur is not None and profit_dedt_cur == profit_dedt_cur:
        cur = by_end.get(end_date)
        if cur is None or pd.isna(cur.get("profit_dedt")):
            by_end[end_date] = {
                "end_date": end_date,
                "ann_date": "99999999",
                "profit_dedt": float(profit_dedt_cur),
            }
    if end_date not in by_end or pd.isna(by_end[end_date].get("profit_dedt")):
        return None, None

    hist = sorted(by_end.values(), key=lambda r: _qi(str(r["end_date"])))
    # 累计扣非 → 单季 q（缺上年同季则 q 仍可算；缺同季上一期则 Q2–Q4 的 q 为空）
    pd_by_qi: dict[int, float] = {}
    for r in hist:
        pdd = r.get("profit_dedt")
        if pdd is not None and pdd == pdd:
            pd_by_qi[_qi(str(r["end_date"]))] = float(pdd)

    recs: list[dict[str, Any]] = []
    for r in hist:
        ed = str(r["end_date"])
        qi = _qi(ed)
        qn = int(ed[4:6]) // 3
        pdd = r.get("profit_dedt")
        q = None
        if pdd is not None and pdd == pdd:
            if qn == 1:
                q = float(pdd)
            elif (qi - 1) in pd_by_qi:
                q = float(pdd) - pd_by_qi[qi - 1]
        recs.append({"qi": qi, "end_date": ed, "q": q})

    q_by_qi = {r["qi"]: r["q"] for r in recs}
    for r in recs:
        q_ly = q_by_qi.get(r["qi"] - 4)
        r["ue"] = (r["q"] - q_ly) if r["q"] is not None and q_ly is not None else None

    # σ = ue.shift(1).rolling_std(8, min_samples=6)（按报告期位置，含空窗）
    ues = [r["ue"] for r in recs]
    sues: list[float | None] = []
    for i in range(len(recs)):
        window = ues[max(0, i - 8): i]  # shift(1) 后的至多 8 期
        vals = [x for x in window if x is not None]
        if len(vals) < 6:
            sues.append(None)
            continue
        # 与 polars rolling_std 对齐：窗口按位置取满 8（不足则用已有），min_samples=6
        # 若窗口长度不足 8 但非空≥6，仍算（rolling 左端）
        sig = float(pd.Series(window, dtype="float64").std(ddof=1))
        if not sig or sig != sig or sig <= 0:
            sues.append(None)
            continue
        ue = ues[i]
        sues.append(None if ue is None else float(ue) / sig)

    target_qi = _qi(end_date)
    for r, sue in zip(recs, sues):
        if r["qi"] == target_qi:
            if sue is None or r["q"] is None:
                return None, None
            return float(sue), float(r["q"])
    return None, None


def sueg_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """as_of 收盘后 SUE≥2 ∩ G 候选；排序 min(SUE,20) 降序、代码升序。数据失败返回 None。"""
    from app.services.midterm_service import earnings_accel_candidates

    ea = earnings_accel_candidates(db, as_of)
    if ea is None:
        return None
    out: list[dict[str, Any]] = []
    for p in ea:
        pdd = p.get("profit_dedt")
        try:
            pdd_f = float(pdd) if pdd is not None else None
        except (TypeError, ValueError):
            pdd_f = None
        sue, q_sq = _sue_for(str(p["code"]), str(p["end_date"]), pdd_f)
        if sue is None or q_sq is None or sue < 2 or q_sq <= 0:
            continue
        item = {k: v for k, v in p.items() if k not in ("ann_pct", "mv_pct", "score")}
        item["sue"] = round(sue, 4)
        item["q_sq"] = q_sq
        item["score"] = round(min(sue, 20.0), 4)
        out.append(item)
    out.sort(key=lambda x: (-x["score"], str(x.get("code") or "")))
    return out
