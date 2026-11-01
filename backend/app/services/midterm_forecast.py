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
    """tushare forecast 按公告日（YYYYMMDD）拉全市场；失败返回 None。

    主用 ann_date=day：promax 的 forecast 要求 ann_date 或 ts_code 至少一个（只给 start_date/end_date 回
    50101「ann_date和ts_code至少输入一个参数」，2026-10-19 17:53 日跑实测，导致 mid-forecast-beat no_data）。
    6059268 曾改成 start/end，依据是“promax 拒绝 ann_date”，复核为误判（ann_date=20261017 正常返回）。
    若上游回参数错误，再用 start_date=end_date=day 试一次。上游全部瞬时失败时等待重试，见
    midterm_service._pro_fetch_with_wait。"""
    from app.services.midterm_service import _pro_fetch_with_wait

    return _pro_fetch_with_wait("forecast", f"ann_date={day}", ann_date=day, fields=",".join(COLS),
                                alt_params={"start_date": day, "end_date": day})


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


def forecast_turnaround_candidates(db, as_of: str, store: bool = True) -> list[dict[str, Any]] | None:
    """§429/§436 FTZ_h20：首次业绩预告 type=扭亏 且 net_profit_min>0；按增幅降序（缺增幅回退净利/1e6；§436）。"""
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
            continue
        code = r["ts_code"][:6]
        if code[0] in "489":
            continue
        if r["type"] != "扭亏":
            continue
        npmin = pd.to_numeric(r["net_profit_min"], errors="coerce")
        if pd.isna(npmin) or not (npmin > 0):
            continue
        # §436：排序改按增幅（有则用 p_change_min；缺则回退净利下限/1e6，与 lt_research181 一致）
        pmin = pd.to_numeric(r.get("p_change_min"), errors="coerce")
        score = float(pmin) if not pd.isna(pmin) else float(npmin) / 1e6
        if code not in out or score > out[code]["score"]:
            out[code] = {"code": code, "ann_date": r["ann_date"], "end_date": r["end_date"], "forecast_type": "扭亏",
                         "p_change_min": None if pd.isna(pmin) else float(pmin),
                         "net_profit_min": float(npmin), "score": score}
    if not out:
        return []
    ok = _liq_ok(db, as_of, dates, sorted(out))
    names = dict(db.execute(text("SELECT code,name FROM stocks WHERE code IN :codes").bindparams(
        bindparam("codes", expanding=True)), {"codes": sorted(ok) or ["-"]}).all())
    res = [{**out[c], "name": names.get(c, ""), "total_mv": float(ok[c]["total_mv"] or 0), "amt20": float(ok[c]["amt20"])}
           for c in ok]
    return sorted(res, key=lambda x: (-x["score"], x["code"]))


def forecast_preincrease_candidates(db, as_of: str, store: bool = True) -> list[dict[str, Any]] | None:
    """§433 FZG_h20：首次业绩预告 type=预增 且 p_change_min>0、net_profit_min>0；按增幅封顶300降序（与研究 fzg_s433 一致）。"""
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
            continue
        code = r["ts_code"][:6]
        if code[0] in "489":
            continue
        if r["type"] != "预增":
            continue
        pmin = pd.to_numeric(r["p_change_min"], errors="coerce")
        npmin = pd.to_numeric(r["net_profit_min"], errors="coerce")
        if pd.isna(pmin) or pd.isna(npmin) or not (pmin > 0 and npmin > 0):
            continue
        score = min(float(pmin), 300.0)
        if code not in out or score > out[code]["score"]:
            out[code] = {"code": code, "ann_date": r["ann_date"], "end_date": r["end_date"], "forecast_type": "预增",
                         "p_change_min": float(pmin), "net_profit_min": float(npmin), "score": score}
    if not out:
        return []
    ok = _liq_ok(db, as_of, dates, sorted(out))
    names = dict(db.execute(text("SELECT code,name FROM stocks WHERE code IN :codes").bindparams(
        bindparam("codes", expanding=True)), {"codes": sorted(ok) or ["-"]}).all())
    res = [{**out[c], "name": names.get(c, ""), "total_mv": float(ok[c]["total_mv"] or 0), "amt20": float(ok[c]["amt20"])}
           for c in ok]
    return sorted(res, key=lambda x: (-x["score"], x["code"]))




def forecast_preincrease_underreact_candidates(db, as_of: str, store: bool = True) -> list[dict[str, Any]] | None:
    """§451 FZGU_h20：预增事件 ∩ 信号日 pct∈[-1,+2] 且未封涨停；排序仍按增幅封顶300（与研究 fzgu_s451 一致）。"""
    from sqlalchemy import text, bindparam
    base = forecast_preincrease_candidates(db, as_of, store=store)
    if base is None:
        return None
    if not base:
        return []
    codes = [p["code"] for p in base]
    rows = db.execute(text(
        "SELECT code, pct_chg, close FROM daily_kbars "
        "WHERE trade_date=:d AND code IN :codes"
    ).bindparams(bindparam("codes", expanding=True)), {"d": as_of, "codes": codes}).mappings().all()
    by = {r["code"]: r for r in rows}
    out = []
    for p0 in base:
        r = by.get(p0["code"])
        if not r or r["pct_chg"] is None or r["close"] is None:
            continue
        pct = float(r["pct_chg"])
        if pct < -1.0 or pct > 2.0:
            continue
        close = float(r["close"])
        code = p0["code"]
        band = 0.198 if code.startswith(("3", "688")) else 0.098
        pre = close / (1 + pct / 100.0) if pct > -99.999 else close
        ul = pre * (1 + band)
        if close >= ul - 0.005:
            continue
        q = dict(p0)
        q["signal_pct"] = round(pct, 3)
        out.append(q)
    return sorted(out, key=lambda x: (-x["score"], x["code"]))


def forecast_preincrease_vol_candidates(db, as_of: str, store: bool = True) -> list[dict[str, Any]] | None:
    """§517 FZGVOL_h20：预增事件 ∩ 信号日成交额≥1.5×amt20；排序仍按增幅封顶300（与研究 fzgvol_s517 一致）。"""
    from sqlalchemy import text, bindparam
    base = forecast_preincrease_candidates(db, as_of, store=store)
    if base is None:
        return None
    if not base:
        return []
    codes = [p["code"] for p in base]
    rows = db.execute(text(
        "SELECT code, amount FROM daily_kbars WHERE trade_date=:d AND code IN :codes"
    ).bindparams(bindparam("codes", expanding=True)), {"d": as_of, "codes": codes}).mappings().all()
    amt_by = {r["code"]: float(r["amount"]) for r in rows if r["amount"] is not None}
    out = []
    for p0 in base:
        amount = amt_by.get(p0["code"])
        amt20 = p0.get("amt20")
        try:
            amt20_f = float(amt20) if amt20 is not None else None
        except (TypeError, ValueError):
            amt20_f = None
        if amount is None or amt20_f is None or amt20_f <= 0 or amount < 1.5 * amt20_f:
            continue
        q = dict(p0)
        q["amount"] = amount
        q["vol_ratio"] = round(amount / amt20_f, 4)
        out.append(q)
    return sorted(out, key=lambda x: (-x["score"], x["code"]))

def forecast_slight_candidates(db, as_of: str, store: bool = True) -> list[dict[str, Any]] | None:
    """§435/§438 FLZ_h20：首次业绩预告 type=略增 且 p_change_min>0、net_profit_min>0；按净利下限降序（§438）。"""
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
            continue
        code = r["ts_code"][:6]
        if code[0] in "489":
            continue
        if r["type"] != "略增":
            continue
        pmin = pd.to_numeric(r["p_change_min"], errors="coerce")
        npmin = pd.to_numeric(r["net_profit_min"], errors="coerce")
        if pd.isna(pmin) or pd.isna(npmin) or not (pmin > 0 and npmin > 0):
            continue
        # §438：排序改按净利下限（与 lt_research182 一致）；事件门槛不变
        score = float(npmin)
        if code not in out or score > out[code]["score"]:
            out[code] = {"code": code, "ann_date": r["ann_date"], "end_date": r["end_date"], "forecast_type": "略增",
                         "p_change_min": float(pmin), "net_profit_min": float(npmin), "score": score}
    if not out:
        return []
    ok = _liq_ok(db, as_of, dates, sorted(out))
    names = dict(db.execute(text("SELECT code,name FROM stocks WHERE code IN :codes").bindparams(
        bindparam("codes", expanding=True)), {"codes": sorted(ok) or ["-"]}).all())
    res = [{**out[c], "name": names.get(c, ""), "total_mv": float(ok[c]["total_mv"] or 0), "amt20": float(ok[c]["amt20"])}
           for c in ok]
    return sorted(res, key=lambda x: (-x["score"], x["code"]))


def forecast_slight_underreact_candidates(db, as_of: str, store: bool = True) -> list[dict[str, Any]] | None:
    """§453 FLZU_h20：略增事件 ∩ 信号日 pct∈[-1,+2] 且未封涨停；排序净利下限（与 §438 / 研究 flzu_s453 一致）。"""
    from sqlalchemy import text, bindparam
    base = forecast_slight_candidates(db, as_of, store=store)
    if base is None:
        return None
    if not base:
        return []
    codes = [p["code"] for p in base]
    rows = db.execute(text(
        "SELECT code, pct_chg, close FROM daily_kbars "
        "WHERE trade_date=:d AND code IN :codes"
    ).bindparams(bindparam("codes", expanding=True)), {"d": as_of, "codes": codes}).mappings().all()
    by = {r["code"]: r for r in rows}
    out = []
    for p0 in base:
        r = by.get(p0["code"])
        if not r or r["pct_chg"] is None or r["close"] is None:
            continue
        pct = float(r["pct_chg"])
        if pct < -1.0 or pct > 2.0:
            continue
        close = float(r["close"])
        code = p0["code"]
        band = 0.198 if code.startswith(("3", "688")) else 0.098
        pre = close / (1 + pct / 100.0) if pct > -99.999 else close
        ul = pre * (1 + band)
        if close >= ul - 0.005:
            continue
        q = dict(p0)
        q["signal_pct"] = round(pct, 3)
        out.append(q)
    return sorted(out, key=lambda x: (-x["score"], x["code"]))


def forecast_beat_underreact_candidates(db, as_of: str, store: bool = True) -> list[dict[str, Any]] | None:
    """§455 FAU_h20：预告合集（预增/扭亏/略增）∩ 信号日 pct∈[-1,+2] 且未封涨停；排序增幅封顶300（与研究 fau_s455 一致）。"""
    from sqlalchemy import text, bindparam
    base = forecast_beat_candidates(db, as_of, store=store)
    if base is None:
        return None
    if not base:
        return []
    codes = [p["code"] for p in base]
    rows = db.execute(text(
        "SELECT code, pct_chg, close FROM daily_kbars "
        "WHERE trade_date=:d AND code IN :codes"
    ).bindparams(bindparam("codes", expanding=True)), {"d": as_of, "codes": codes}).mappings().all()
    by = {r["code"]: r for r in rows}
    out = []
    for p0 in base:
        r = by.get(p0["code"])
        if not r or r["pct_chg"] is None or r["close"] is None:
            continue
        pct = float(r["pct_chg"])
        if pct < -1.0 or pct > 2.0:
            continue
        close = float(r["close"])
        code = p0["code"]
        band = 0.198 if code.startswith(("3", "688")) else 0.098
        pre = close / (1 + pct / 100.0) if pct > -99.999 else close
        ul = pre * (1 + band)
        if close >= ul - 0.005:
            continue
        q = dict(p0)
        q["signal_pct"] = round(pct, 3)
        out.append(q)
    return sorted(out, key=lambda x: (-x["score"], x["code"]))

