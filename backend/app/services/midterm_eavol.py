"""[中线] §513 EAVOL_h20：业绩加速 ∩ 放量≥1.5×amt20（mid-earnings-accel-vol）实盘候选。

研究：scripts/midterm/eavol_s513.py（G ∩ amount≥1.5·amt20，排序 sc_g=dtp 封顶300）。
G 与现役 mid-earnings-accel 相同；另要求信号日成交额 ≥1.5×前20日均额。
事件时点 / 股池：同 earnings_accel_candidates。不另改数据网关。
实盘推荐不设仓位上限（k_slots/max_new 仅回测口径）。
§699 EATDVOL→ mid-ea-td-vol：G∩TD∩放量。
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any

from sqlalchemy import bindparam, text

logger = logging.getLogger(__name__)


def eavol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """as_of 收盘后可见的业绩加速∩放量确认信号。数据失败返回 None。"""
    import pandas as pd
    from app.services.midterm_service import (
        _fetch_fina_period,
        _finite,
        _liq_ok,
        _quarter_ends,
    )

    dates = [r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<=:d "
        "ORDER BY trade_date DESC LIMIT 251"), {"d": as_of})]
    if len(dates) < 251 or dates[0] != as_of:
        return None
    start = (_dt.date.fromisoformat(dates[1]) + _dt.timedelta(days=1)).strftime("%Y%m%d")
    end = as_of.replace("-", "")
    periods = _quarter_ends(as_of, 3)
    frames = {}
    for per in periods:
        f = _fetch_fina_period(per)
        if f is None:
            return None
        f = f.copy()
        f["ann_date"] = f["ann_date"].astype(str).str.strip()
        frames[per] = f.sort_values("ann_date").drop_duplicates("ts_code", keep="first")
    out: dict[str, dict[str, Any]] = {}
    for cur, prev in ((periods[0], periods[1]), (periods[1], periods[2])):
        f = frames[cur]
        today = f[(f["ann_date"] >= start) & (f["ann_date"] <= end)]
        if today.empty:
            continue
        pv = dict(zip(frames[prev]["ts_code"], pd.to_numeric(frames[prev]["dt_netprofit_yoy"], errors="coerce")))
        for _, r in today.iterrows():
            code = str(r["ts_code"])[:6]
            if code[0] in "489":
                continue
            dtp = pd.to_numeric(r["dt_netprofit_yoy"], errors="coerce")
            orv = pd.to_numeric(r["or_yoy"], errors="coerce")
            pdd = pd.to_numeric(r["profit_dedt"], errors="coerce")
            prev_dtp = pv.get(r["ts_code"])
            if any(pd.isna(x) for x in (dtp, orv, pdd)) or prev_dtp is None or pd.isna(prev_dtp):
                continue
            if dtp >= 50 and orv >= 20 and pdd > 0 and dtp > prev_dtp:
                out[code] = {
                    "code": code,
                    "ann_date": str(r["ann_date"]),
                    "end_date": cur,
                    "dt_netprofit_yoy": float(dtp),
                    "or_yoy": float(orv),
                    "prev_dt_netprofit_yoy": float(prev_dtp),
                    "profit_dedt": float(pdd),
                }
    if not out:
        return []
    ok = _liq_ok(db, as_of, dates, sorted(out))
    if not ok:
        return []
    codes = sorted(ok)
    amt_rows = db.execute(text(
        "SELECT code, amount FROM daily_kbars WHERE trade_date=:d AND code IN :codes"
    ).bindparams(bindparam("codes", expanding=True)), {"d": as_of, "codes": codes}).mappings().all()
    amt_by = {r["code"]: float(r["amount"]) for r in amt_rows if r["amount"] is not None}
    names = dict(db.execute(text("SELECT code,name FROM stocks WHERE code IN :codes").bindparams(
        bindparam("codes", expanding=True)), {"codes": codes or ["-"]}).all())
    res: list[dict[str, Any]] = []
    for c in codes:
        amount = amt_by.get(c)
        amt20 = float(ok[c]["amt20"])
        if amount is None or amt20 <= 0 or amount < 1.5 * amt20:
            continue
        dtp = float(out[c]["dt_netprofit_yoy"])
        res.append({
            **out[c],
            "name": names.get(c, ""),
            "total_mv": _finite(ok[c].get("total_mv")),
            "ann_pct_chg": _finite(ok[c].get("pct_chg")),
            "amt20": amt20,
            "amount": amount,
            "vol_ratio": round(amount / amt20, 4),
            "score": round(min(dtp, 300.0), 6),
        })
    res.sort(key=lambda x: (-float(x["score"]), str(x["code"])))
    return res

def eatdvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§699/§700：G ∩ TD缩短 ∩ 放量≥1.5×amt20；排序 TD缩短天数封顶200（§700 改序；事件不变）。"""
    from app.services.midterm_fina_events import td_candidates

    ea = eavol_candidates(db, as_of)
    td = td_candidates(db, as_of)
    if ea is None or td is None:
        return None
    td_by = {p["code"]: p for p in td}
    out: list[dict[str, Any]] = []
    for p0 in ea:
        e = td_by.get(p0["code"])
        if e is None:
            continue
        q = dict(p0)
        q["td_improve"] = e.get("improve")
        q["td_cur"] = e.get("cur")
        q["td_prev"] = e.get("prev")
        # §700：排序改按 TD 缩短（封顶200）；保留 dtp 供展示
        try:
            imp = float(e.get("improve") if e.get("improve") is not None else e.get("score") or 0)
        except (TypeError, ValueError):
            imp = 0.0
        q["score"] = round(min(imp, 200.0), 4)
        out.append(q)
    out.sort(key=lambda x: (-float(x["score"]), str(x.get("code") or "")))
    return out

