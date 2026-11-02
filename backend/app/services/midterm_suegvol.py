"""[中线] §515 SUEGVOL_h20：SUEG ∩ 放量≥1.5×amt20（mid-sue-accel-vol）实盘候选。

研究：scripts/midterm/suegvol_s22_s604.py（§604：SUE≥2.2 ∩ G ∩ amount≥1.5·amt20，排序 min(SUE,20)；由 §515 硬抬）。
复用 midterm_sueg.sueg_candidates 事件池，再按信号日成交额过滤；不另改数据网关。
另：§669 SUEGTDVOL→ mid-sue-td-vol；§671 SUEGFATVOL→ mid-sue-fa-vol；§793 SEXPSUEGVOL→ mid-sexp-sue-vol。
实盘推荐不设仓位上限。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import bindparam, text

logger = logging.getLogger(__name__)


def suegvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """as_of 收盘后 SUE≥2.2∩G∩放量确认；排序 min(SUE,20)。数据失败返回 None。"""
    from app.services.midterm_sueg import sueg_candidates

    pool = sueg_candidates(db, as_of)
    if pool is None:
        return None
    # §604：在 SUEG 池上硬抬 SUE≥2.2（sueg_candidates 仍为 ≥2，供 mid-sue-accel 共用）
    pool = [p for p in pool if float(p.get("sue") or 0) >= 2.2]
    if not pool:
        return []
    codes = [str(p["code"]) for p in pool]
    amt_rows = db.execute(text(
        "SELECT code, amount FROM daily_kbars WHERE trade_date=:d AND code IN :codes"
    ).bindparams(bindparam("codes", expanding=True)), {"d": as_of, "codes": codes}).mappings().all()
    amt_by = {r["code"]: float(r["amount"]) for r in amt_rows if r["amount"] is not None}
    out: list[dict[str, Any]] = []
    for p in pool:
        code = str(p["code"])
        amount = amt_by.get(code)
        amt20 = p.get("amt20")
        try:
            amt20_f = float(amt20) if amt20 is not None else None
        except (TypeError, ValueError):
            amt20_f = None
        if amount is None or amt20_f is None or amt20_f <= 0 or amount < 1.5 * amt20_f:
            continue
        item = dict(p)
        item["amount"] = amount
        item["vol_ratio"] = round(amount / amt20_f, 4)
        out.append(item)
    out.sort(key=lambda x: (-float(x["score"]), str(x.get("code") or "")))
    return out


def suegtdvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§669/§762/§782：SUE≥2.2 ∩ G(dtp≥55,orv≥25) ∩ TD缩短 ∩ 放量≥1.5×amt20（§762 dtp；§782 orv硬抬；suegvol 仍 dtp≥50/orv≥20）；排序 min(SUE,20)。"""
    from app.services.midterm_fina_events import td_candidates

    sg = suegvol_candidates(db, as_of)
    td = td_candidates(db, as_of)
    if sg is None or td is None:
        return None
    td_by = {p["code"]: p for p in td}
    out: list[dict[str, Any]] = []
    for p0 in sg:
        e = td_by.get(p0["code"])
        if e is None:
            continue
        # §762：在 suegvol(G dtp≥50) 基础上再硬抬 dtp≥55（不改动 mid-sue-accel-vol）
        # §782：再硬抬 orv≥25（suegvol / mid-sue-accel-vol 仍 orv≥20）
        try:
            dtp = float(p0.get("dt_netprofit_yoy"))
            orv = float(p0.get("or_yoy"))
        except (TypeError, ValueError):
            continue
        if dtp < 55.0 or orv < 25.0:
            continue
        q = dict(p0)
        q["td_improve"] = e.get("improve")
        q["td_cur"] = e.get("cur")
        q["td_prev"] = e.get("prev")
        out.append(q)
    out.sort(key=lambda x: (-float(x["score"]), str(x.get("code") or "")))
    return out


def suegfatvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§671/§672：SUE≥2.2 ∩ G ∩ FAT升幅≥0.12 ∩ 放量≥1.5×amt20；排序 min(SUE,20)（§672 硬抬）。"""
    from app.services.midterm_fina_events import fat_candidates

    sg = suegvol_candidates(db, as_of)
    fat = fat_candidates(db, as_of)
    if sg is None or fat is None:
        return None
    fat_by = {p["code"]: p for p in fat}
    out: list[dict[str, Any]] = []
    for p0 in sg:
        e = fat_by.get(p0["code"])
        if e is None:
            continue
        dq = e.get("dq", e.get("score"))
        try:
            dq_f = float(dq) if dq is not None else None
        except (TypeError, ValueError):
            dq_f = None
        if dq_f is None or dq_f < 0.12:
            continue
        q = dict(p0)
        q["fat_dq"] = e.get("dq")
        q["fat_cur"] = e.get("cur")
        q["fat_prev"] = e.get("prev")
        out.append(q)
    out.sort(key=lambda x: (-float(x["score"]), str(x.get("code") or "")))
    return out

def sexpsuegvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§793/§798/§800 SEXPSUEGVOL：SEXP降幅≥0.35 ∩ SUE≥2.2∩G(dtp≥55,orv≥25)∩放量≥1.5×amt20（§798 dtp；§800 orv硬抬；suegvol 仍≥50/≥20）；排序 SEXP 原始降幅（去封顶）。"""
    from app.services.midterm_fina_events import (
        SEXP_CUR_HI,
        SEXP_CUR_LO,
        SEXP_PREV_HI,
        SEXP_PREV_LO,
        _fina_events,
    )

    sg = suegvol_candidates(db, as_of)
    if sg is None:
        return None

    def passes(cur: float, prev: float):
        delta = prev - cur
        ok = (SEXP_CUR_LO <= cur <= SEXP_CUR_HI and SEXP_PREV_LO <= prev <= SEXP_PREV_HI and delta >= 0.35)
        return ok, delta, {"delta": delta}

    sx = _fina_events(db, as_of, "saleexp_to_gr", passes)
    if sx is None:
        return None
    sx_by = {p["code"]: p for p in sx}
    out: list[dict[str, Any]] = []
    for p0 in sg:
        e = sx_by.get(p0["code"])
        if e is None:
            continue
        # §798/§800：在 suegvol(G dtp≥50/orv≥20) 基础上再硬抬 dtp≥55、orv≥25（不改动 mid-sue-accel-vol）
        try:
            dtp = float(p0.get("dt_netprofit_yoy"))
            orv = float(p0.get("or_yoy"))
        except (TypeError, ValueError):
            continue
        if dtp < 55.0 or orv < 25.0:
            continue
        q = dict(p0)
        q["sexp_delta"] = e.get("delta", e.get("score"))
        q["sexp_cur"] = e.get("cur")
        q["sexp_prev"] = e.get("prev")
        try:
            q["score"] = float(q["sexp_delta"])
        except (TypeError, ValueError):
            continue
        out.append(q)
    out.sort(key=lambda x: (-float(x["score"]), str(x.get("code") or "")))
    return out


def adminsuegvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§801 ADMINSUEGVOL：ADMIN降幅≥1.0 ∩ SUE≥2.2∩G∩放量≥1.5×amt20；排序 ADMIN 降幅封顶10。"""
    from app.services.midterm_fina_events import (
        ADMIN_CUR_HI,
        ADMIN_CUR_LO,
        ADMIN_PREV_HI,
        ADMIN_PREV_LO,
        ADMIN_DELTA_MIN,
        ADMIN_DELTA_CAP,
        _fina_events,
    )

    sg = suegvol_candidates(db, as_of)
    if sg is None:
        return None

    def passes(cur: float, prev: float):
        delta = prev - cur
        ok = (ADMIN_CUR_LO <= cur <= ADMIN_CUR_HI and ADMIN_PREV_LO <= prev <= ADMIN_PREV_HI and delta >= ADMIN_DELTA_MIN)
        return ok, min(delta, ADMIN_DELTA_CAP), {"delta": delta}

    ad = _fina_events(db, as_of, "adminexp_of_gr", passes)
    if ad is None:
        return None
    ad_by = {p["code"]: p for p in ad}
    out: list[dict[str, Any]] = []
    for p0 in sg:
        e = ad_by.get(p0["code"])
        if e is None:
            continue
        q = dict(p0)
        q["admin_delta"] = e.get("delta", e.get("score"))
        q["admin_cur"] = e.get("cur")
        q["admin_prev"] = e.get("prev")
        try:
            q["score"] = float(q["admin_delta"])
        except (TypeError, ValueError):
            continue
        out.append(q)
    out.sort(key=lambda x: (-float(x["score"]), str(x.get("code") or "")))
    return out

