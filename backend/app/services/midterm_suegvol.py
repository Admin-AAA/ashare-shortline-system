"""[中线] §515 SUEGVOL_h20：SUEG ∩ 放量≥1.5×amt20（mid-sue-accel-vol）实盘候选。

研究：scripts/midterm/suegvol_s515.py（SUE≥2 ∩ G ∩ amount≥1.5·amt20，排序 min(SUE,20)）。
复用 midterm_sueg.sueg_candidates 事件池，再按信号日成交额过滤；不另改数据网关。
实盘推荐不设仓位上限。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import bindparam, text

logger = logging.getLogger(__name__)


def suegvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """as_of 收盘后 SUEG∩放量确认；排序 min(SUE,20)。数据失败返回 None。"""
    from app.services.midterm_sueg import sueg_candidates

    pool = sueg_candidates(db, as_of)
    if pool is None:
        return None
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
