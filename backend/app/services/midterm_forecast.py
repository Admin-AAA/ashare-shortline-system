"""[中线] §103 业绩预告正向漂移（mid-forecast-beat）实盘候选。

研究：scripts/midterm/lt_research20.py F_any_h20（预注册 3 版中采纳的一版，未调参）。
口径：tushare forecast（经 tushare_client.get_pro 统一数据网关，只读），同一 (股票, 报告期 end_date) 只认第一次预告
（修正公告不算新信号）；第一次预告 type ∈ {预增, 扭亏, 略增} 且 p_change_min > 0 且 net_profit_min > 0；
公告日在 (上一交易日, as_of]（周末/盘后公告顺延到下一交易日），as_of 收盘出信号、次日开盘买、持 20 日。
同一股票同日多个报告期合格取分数最大；分数 = min(p_change_min, 300)，降序、平手按代码升序。
流动性：midterm_service._liq_ok（lt_research liq 口径）。
"第一次"判断：本地 data/midterm/forecast/<code>.parquet 全历史（backfill_longterm.py）+ 实盘每日拉到的公告
（data/midterm/events/forecast_live_<year>.parquet，本模块追加）。"""
from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, text

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "midterm"
FC_DIR = DATA_DIR / "forecast"
LIVE_DIR = DATA_DIR / "events"
TYPES = ("预增", "扭亏", "略增")
COLS = ["ts_code", "ann_date", "end_date", "type", "p_change_min", "net_profit_min"]


def _fetch_forecast_day(day: str):
    """tushare forecast 按公告日（YYYYMMDD）拉全市场；失败返回 None。"""
    from app.services.tushare_client import get_pro
    try:
        return get_pro().forecast(ann_date=day, fields=",".join(COLS))
    except Exception as exc:  # noqa: BLE001
        logger.warning("forecast ann_date=%s failed: %s", day, exc)
        return None


def _store_live(new) -> None:
    """把实盘拉到的预告并入 data/midterm/events/forecast_live_<year>.parquet，供以后"首次预告"判断。非致命。"""
    import pandas as pd
    try:
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        new = new[COLS].copy()
        for year, part in new.groupby(new["ann_date"].str.slice(0, 4)):
            f = LIVE_DIR / f"forecast_live_{year}.parquet"
            merged = pd.concat([pd.read_parquet(f), part]) if f.exists() else part
            merged = merged.drop_duplicates(["ts_code", "ann_date", "end_date", "type"])
            merged.to_parquet(f, index=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("store forecast live events failed: %s", exc)


def _history(codes: list[str]):
    """候选股票的历史预告（ts_code, ann_date, end_date）：本地逐股全历史 + 实盘追加库。"""
    import pandas as pd
    parts = []
    for c in codes:
        f = FC_DIR / f"{c}.parquet"
        if f.exists():
            try:
                parts.append(pd.read_parquet(f, columns=["ts_code", "ann_date", "end_date"]))
            except Exception as exc:  # noqa: BLE001
                logger.warning("forecast history %s read failed: %s", f.name, exc)
    for f in sorted(LIVE_DIR.glob("forecast_live_*.parquet")):
        try:
            x = pd.read_parquet(f, columns=["ts_code", "ann_date", "end_date"])
            parts.append(x[x["ts_code"].astype(str).str.slice(0, 6).isin(codes)])
        except Exception as exc:  # noqa: BLE001
            logger.warning("forecast live %s read failed: %s", f.name, exc)
    if not parts:
        return pd.DataFrame(columns=["ts_code", "ann_date", "end_date"])
    h = pd.concat(parts, ignore_index=True)
    for k in ("ts_code", "ann_date", "end_date"):
        h[k] = h[k].astype(str).str.strip()
    return h


def forecast_beat_candidates(db, as_of: str, store: bool = True) -> list[dict[str, Any]] | None:
    """as_of 收盘后可见的首次业绩预告正向信号（lt_research20 F_any_h20）。数据拉取失败返回 None。"""
    import pandas as pd
    from app.services.midterm_service import _liq_ok
    dates = [r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<=:d "
        "ORDER BY trade_date DESC LIMIT 251"), {"d": as_of})]
    if len(dates) < 251 or dates[0] != as_of:
        return None
    d0 = _dt.date.fromisoformat(dates[1]) + _dt.timedelta(days=1)
    d1 = _dt.date.fromisoformat(as_of)
    start, end = d0.strftime("%Y%m%d"), d1.strftime("%Y%m%d")
    frames = []
    d = d0
    while d <= d1:
        f = _fetch_forecast_day(d.strftime("%Y%m%d"))
        if f is None:
            return None
        if len(f):
            frames.append(f)
        d += _dt.timedelta(days=1)
    if not frames:
        return []
    new = pd.concat(frames, ignore_index=True)
    for k in ("ts_code", "ann_date", "end_date", "type"):
        new[k] = new[k].astype(str).str.strip()
    new = new[(new["ann_date"] >= start) & (new["ann_date"] <= end)]
    if new.empty:
        return []
    if store:
        _store_live(new)
    codes = sorted(set(new["ts_code"].str.slice(0, 6)))
    hist = _history(codes)
    earlier = set(zip(hist.loc[hist["ann_date"] < start, "ts_code"], hist.loc[hist["ann_date"] < start, "end_date"]))
    first = new.sort_values(["ann_date"], kind="stable").drop_duplicates(["ts_code", "end_date"], keep="first")
    out: dict[str, dict[str, Any]] = {}
    for _, r in first.iterrows():
        if (r["ts_code"], r["end_date"]) in earlier:
            continue  # 该报告期之前已预告过（本条是修正/再次预告）
        code = r["ts_code"][:6]
        if code[0] in "489":
            continue
        pmin = pd.to_numeric(r["p_change_min"], errors="coerce")
        npmin = pd.to_numeric(r["net_profit_min"], errors="coerce")
        if r["type"] not in TYPES or pd.isna(pmin) or pd.isna(npmin) or not (pmin > 0 and npmin > 0):
            continue
        score = min(float(pmin), 300.0)
        if code not in out or score > out[code]["score"]:
            out[code] = {"code": code, "ann_date": r["ann_date"], "end_date": r["end_date"], "forecast_type": r["type"],
                         "p_change_min": float(pmin), "net_profit_min": float(npmin), "score": score}
    if not out:
        return []
    ok = _liq_ok(db, as_of, dates, sorted(out))
    names = dict(db.execute(text("SELECT code,name FROM stocks WHERE code IN :codes").bindparams(
        bindparam("codes", expanding=True)), {"codes": sorted(ok) or ["-"]}).all())
    res = [{**out[c], "name": names.get(c, ""), "total_mv": float(ok[c]["total_mv"] or 0), "amt20": float(ok[c]["amt20"])}
           for c in ok]
    return sorted(res, key=lambda x: (-x["score"], x["code"]))
