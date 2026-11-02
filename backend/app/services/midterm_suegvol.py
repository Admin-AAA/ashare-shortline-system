"""[中线] §515 SUEGVOL_h20：SUEG ∩ 放量≥1.5×amt20（mid-sue-accel-vol）实盘候选。

研究：scripts/midterm/suegvol_s22_s604.py（§604：SUE≥2.2 ∩ G ∩ amount≥1.5·amt20，排序 min(SUE,20)；由 §515 硬抬）。
复用 midterm_sueg.sueg_candidates 事件池，再按信号日成交额过滤；不另改数据网关。
另：§669 SUEGTDVOL→ mid-sue-td-vol；§671 SUEGFATVOL→ mid-sue-fa-vol；§793 SEXPSUEGVOL→ mid-sexp-sue-vol；§801 ADMINSUEGVOL→ mid-admin-sue-vol；§816 OPSUEGVOL→ mid-op-sue-vol；§817 REVSUEGVOL→ mid-rev-sue-vol；§827 QOPSUEGVOL→ mid-qop-sue-vol；§829/§832 OCFSSUEGVOL→ mid-ocfs-sue-vol；§833 OCSDSUEGVOL→ mid-ocsd-sue-vol；§837 NPMSUEGVOL→ mid-npm-sue-vol；§844 QSSUEGVOL→ mid-qs-sue-vol。
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
    """§793/§798/§800/§802/§804/§806/§808/§810 SEXPSUEGVOL：SEXP降幅≥0.35 ∩ SUE≥2.2∩G(dtp≥60,orv≥45)∩放量≥1.5×amt20（§802 dtp；§810 orv再抬；suegvol 仍≥50/≥20）；排序 SEXP 原始降幅（去封顶）。"""
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
        # §798/§800/§802/§804/§806/§808/§810：在 suegvol(G dtp≥50/orv≥20) 基础上再硬抬 dtp≥60、orv≥45（不改动 mid-sue-accel-vol）
        try:
            dtp = float(p0.get("dt_netprofit_yoy"))
            orv = float(p0.get("or_yoy"))
        except (TypeError, ValueError):
            continue
        if dtp < 60.0 or orv < 45.0:
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
    """§801/§803/§805/§809 ADMINSUEGVOL：ADMIN降幅≥1.0 ∩ SUE≥2.2∩G(dtp≥55,orv≥30)∩放量≥1.5×amt20（§803 dtp；§809 orv再抬；suegvol 仍≥50/≥20）；排序 ADMIN 降幅封顶10。"""
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
        # §803/§805/§809：在 suegvol(G dtp≥50/orv≥20) 基础上再硬抬 dtp≥55、orv≥30
        try:
            dtp = float(p0.get("dt_netprofit_yoy"))
            orv = float(p0.get("or_yoy"))
        except (TypeError, ValueError):
            continue
        if dtp < 55.0 or orv < 30.0:
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


def opsuegvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§816/§818/§822 OPSUEGVOL：OP加速≥20 ∩ SUE≥2.2∩G(dtp≥60)∩放量≥1.5×amt20（§822 dtp再抬；suegvol 仍≥50/≥20）；排序 OP加速封顶100。"""
    from app.services.midterm_fina_events import _fina_events

    sg = suegvol_candidates(db, as_of)
    if sg is None:
        return None

    def passes(cur: float, prev: float):
        accel = cur - prev
        ok = cur >= 20.0 and accel >= 20.0
        return ok, min(accel, 100.0), {"op_yoy": cur, "prev_op_yoy": prev, "op_accel": accel}

    op = _fina_events(db, as_of, "op_yoy", passes)
    if op is None:
        return None
    op_by = {p["code"]: p for p in op}
    out: list[dict[str, Any]] = []
    for p0 in sg:
        e = op_by.get(p0["code"])
        if e is None:
            continue
        # §818/§822：在 suegvol(G dtp≥50/orv≥20) 基础上再硬抬 dtp≥60
        try:
            dtp = float(p0.get("dt_netprofit_yoy"))
        except (TypeError, ValueError):
            continue
        if dtp < 60.0:
            continue
        q = dict(p0)
        q["op_yoy"] = e.get("op_yoy", e.get("cur"))
        q["prev_op_yoy"] = e.get("prev_op_yoy", e.get("prev"))
        q["op_accel"] = e.get("op_accel", e.get("score"))
        try:
            q["score"] = float(q["op_accel"])
        except (TypeError, ValueError):
            continue
        out.append(q)
    out.sort(key=lambda x: (-float(x["score"]), str(x.get("code") or "")))
    return out


def revsuegvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§817/§819 REVSUEGVOL：REV加速≥20 ∩ SUE≥2.2∩G(dtp≥55)∩放量≥1.5×amt20（§819 dtp硬抬；suegvol 仍≥50/≥20）；排序 REV加速封顶100。"""
    from app.services.midterm_fina_events import _fina_events

    sg = suegvol_candidates(db, as_of)
    if sg is None:
        return None

    def passes(cur: float, prev: float):
        accel = cur - prev
        ok = cur >= 20.0 and accel >= 20.0
        return ok, min(accel, 100.0), {"rev_or_yoy": cur, "prev_or_yoy": prev, "rev_accel": accel}

    rv = _fina_events(db, as_of, "or_yoy", passes)
    if rv is None:
        return None
    rv_by = {p["code"]: p for p in rv}
    out: list[dict[str, Any]] = []
    for p0 in sg:
        e = rv_by.get(p0["code"])
        if e is None:
            continue
        # §819：在 suegvol(G dtp≥50/orv≥20) 基础上再硬抬 dtp≥55
        try:
            dtp = float(p0.get("dt_netprofit_yoy"))
        except (TypeError, ValueError):
            continue
        if dtp < 55.0:
            continue
        q = dict(p0)
        q["rev_or_yoy"] = e.get("rev_or_yoy", e.get("cur"))
        q["prev_or_yoy"] = e.get("prev_or_yoy", e.get("prev"))
        q["rev_accel"] = e.get("rev_accel", e.get("score"))
        try:
            q["score"] = float(q["rev_accel"])
        except (TypeError, ValueError):
            continue
        out.append(q)
    out.sort(key=lambda x: (-float(x["score"]), str(x.get("code") or "")))
    return out


def qopsuegvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§827 QOPSUEGVOL：QOP加速≥20（q_op_qoq≥20且同比加速≥20）∩ SUE≥2.2∩G∩放量≥1.5×amt20；排序 QOP加速封顶100。"""
    from app.services.midterm_fina_events import _fina_events

    sg = suegvol_candidates(db, as_of)
    if sg is None:
        return None

    def passes(cur: float, prev: float):
        accel = cur - prev
        ok = cur >= 20.0 and accel >= 20.0
        return ok, min(accel, 100.0), {"q_op_qoq": cur, "prev_q_op_qoq": prev, "qop_accel": accel}

    qop = _fina_events(db, as_of, "q_op_qoq", passes)
    if qop is None:
        return None
    qop_by = {p["code"]: p for p in qop}
    out: list[dict[str, Any]] = []
    for p0 in sg:
        e = qop_by.get(p0["code"])
        if e is None:
            continue
        q = dict(p0)
        q["q_op_qoq"] = e.get("q_op_qoq", e.get("cur"))
        q["prev_q_op_qoq"] = e.get("prev_q_op_qoq", e.get("prev"))
        q["qop_accel"] = e.get("qop_accel", e.get("score"))
        try:
            q["score"] = float(q["qop_accel"])
        except (TypeError, ValueError):
            continue
        out.append(q)
    out.sort(key=lambda x: (-float(x["score"]), str(x.get("code") or "")))
    return out


def ocfssuegvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§829/§832 OCFSSUEGVOL：OCFS升幅≥3.0 ∩ SUE≥2.2∩G(orv≥25)∩放量≥1.5×amt20（§832 orv硬抬；suegvol 仍≥50/≥20）；排序升幅封顶30。"""
    from app.services.midterm_fina_events import _fina_events

    sg = suegvol_candidates(db, as_of)
    if sg is None:
        return None

    def passes(cur: float, prev: float):
        dq = cur - prev
        ok = (-20.0 <= cur <= 80.0 and -50.0 <= prev <= 120.0 and dq >= 3.0)
        return ok, min(dq, 30.0), {"ocfs_cur": cur, "ocfs_prev": prev, "ocfs_dq": dq}

    oc = _fina_events(db, as_of, "q_ocf_to_sales", passes)
    if oc is None:
        return None
    oc_by = {p["code"]: p for p in oc}
    out: list[dict[str, Any]] = []
    for p0 in sg:
        e = oc_by.get(p0["code"])
        if e is None:
            continue
        # §832：在 suegvol(G dtp≥50/orv≥20) 基础上再硬抬 orv≥25
        try:
            orv = float(p0.get("or_yoy"))
        except (TypeError, ValueError):
            continue
        if orv < 25.0:
            continue
        q = dict(p0)
        q["ocfs_cur"] = e.get("ocfs_cur", e.get("cur"))
        q["ocfs_prev"] = e.get("ocfs_prev", e.get("prev"))
        q["ocfs_dq"] = e.get("ocfs_dq", e.get("score"))
        try:
            q["score"] = float(q["ocfs_dq"])
        except (TypeError, ValueError):
            continue
        out.append(q)
    out.sort(key=lambda x: (-float(x["score"]), str(x.get("code") or "")))
    return out


def ocsdsuegvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§833 OCSDSUEGVOL：OCSD升幅≥0.05（ocf_to_shortdebt∈[-0.20,0.60]、去年∈[-0.50,1.20]）∩ SUE≥2.2∩G∩放量≥1.5×amt20；排序升幅封顶0.40。"""
    from app.services.midterm_fina_events import _fina_events

    sg = suegvol_candidates(db, as_of)
    if sg is None:
        return None

    def passes(cur: float, prev: float):
        dq = cur - prev
        ok = (-0.20 <= cur <= 0.60 and -0.50 <= prev <= 1.20 and dq >= 0.05)
        return ok, min(dq, 0.40), {"ocsd_cur": cur, "ocsd_prev": prev, "ocsd_dq": dq}

    oc = _fina_events(db, as_of, "ocf_to_shortdebt", passes)
    if oc is None:
        return None
    oc_by = {p["code"]: p for p in oc}
    out: list[dict[str, Any]] = []
    for p0 in sg:
        e = oc_by.get(p0["code"])
        if e is None:
            continue
        q = dict(p0)
        q["ocsd_cur"] = e.get("ocsd_cur", e.get("cur"))
        q["ocsd_prev"] = e.get("ocsd_prev", e.get("prev"))
        q["ocsd_dq"] = e.get("ocsd_dq", e.get("score"))
        try:
            q["score"] = float(q["ocsd_dq"])
        except (TypeError, ValueError):
            continue
        out.append(q)
    out.sort(key=lambda x: (-float(x["score"]), str(x.get("code") or "")))
    return out



def npmsuegvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§837 NPMSUEGVOL：NPM升幅≥2.0pp（本期 netprofit_margin≥3）∩ SUE≥2.2∩G∩放量≥1.5×amt20；排序 ΔNPM封顶20。"""
    from app.services.midterm_fina_events import _fina_events

    sg = suegvol_candidates(db, as_of)
    if sg is None:
        return None

    def passes(cur: float, prev: float):
        dq = cur - prev
        ok = (cur >= 3.0 and -50.0 <= prev <= 80.0 and dq >= 2.0)
        return ok, min(dq, 20.0), {"npm_cur": cur, "npm_prev": prev, "npm_dq": dq}

    oc = _fina_events(db, as_of, "netprofit_margin", passes)
    if oc is None:
        return None
    oc_by = {p["code"]: p for p in oc}
    out: list[dict[str, Any]] = []
    for p0 in sg:
        e = oc_by.get(p0["code"])
        if e is None:
            continue
        q = dict(p0)
        q["npm_cur"] = e.get("npm_cur", e.get("cur"))
        q["npm_prev"] = e.get("npm_prev", e.get("prev"))
        q["npm_dq"] = e.get("npm_dq", e.get("score"))
        try:
            q["score"] = float(q["npm_dq"])
        except (TypeError, ValueError):
            continue
        out.append(q)
    out.sort(key=lambda x: (-float(x["score"]), str(x.get("code") or "")))
    return out

def qssuegvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§844/§846 QSSUEGVOL：QS加速≥20（q_sales_yoy≥20且同比加速≥20）∩ SUE≥2.2∩G(dtp≥55)∩放量≥1.5×amt20（§846 dtp硬抬；suegvol 仍≥50/≥20）；排序加速封顶100。"""
    from app.services.midterm_fina_events import _fina_events

    sg = suegvol_candidates(db, as_of)
    if sg is None:
        return None

    def passes(cur: float, prev: float):
        accel = cur - prev
        ok = cur >= 20.0 and accel >= 20.0
        return ok, min(accel, 100.0), {"q_sales_yoy": cur, "prev_q_sales_yoy": prev, "qs_accel": accel}

    qs = _fina_events(db, as_of, "q_sales_yoy", passes)
    if qs is None:
        return None
    qs_by = {p["code"]: p for p in qs}
    out: list[dict[str, Any]] = []
    for p0 in sg:
        e = qs_by.get(p0["code"])
        if e is None:
            continue
        # §846：在 suegvol(G dtp≥50/orv≥20) 基础上再硬抬 dtp≥55
        try:
            dtp = float(p0.get("dt_netprofit_yoy"))
        except (TypeError, ValueError):
            continue
        if dtp < 55.0:
            continue
        q = dict(p0)
        q["q_sales_yoy"] = e.get("q_sales_yoy", e.get("cur"))
        q["prev_q_sales_yoy"] = e.get("prev_q_sales_yoy", e.get("prev"))
        q["qs_accel"] = e.get("qs_accel", e.get("score"))
        try:
            q["score"] = float(q["qs_accel"])
        except (TypeError, ValueError):
            continue
        out.append(q)
    out.sort(key=lambda x: (-float(x["score"]), str(x.get("code") or "")))
    return out

