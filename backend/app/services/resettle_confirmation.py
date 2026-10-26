"""用户批准的显式重算（scripts/resettle_single_since_20261014.py）用的「新确认规则」。

被显式重算过的记录（label_source = RESETTLE_LABEL_SOURCE）以后每次自动重算 / 补结算，执行资格都按这里
从日 K 重新推出，而不是读当时旧确认逻辑写下的执行状态——否则一次日 K 改写就会把它们悄悄投影回「没买进」。
执行状态表本身不改（那是实盘当时的确认记录）。
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import DailyKbar

RESETTLE_LABEL_SOURCE = "resettle_20261027"
CONFIRMED_STATE = SimpleNamespace(ever_buy_confirmed=True, cancelled_before_entry=False, latest_decision_code="buy")
NOT_CONFIRMED_STATE = SimpleNamespace(ever_buy_confirmed=False, cancelled_before_entry=False,
                                      latest_decision_code="expired")


def new_rule_confirmation(db: Session, rec: Any) -> tuple[str | None, float | None, bool]:
    """新确认规则下的确认买入日/价：入场窗口内第一个通过入场日检查的日子（开盘价）。

    返回 (日, 价, 能否判定)。窗口里有日 K 缺失时「不能判定」（不替它做确认，也不判过期）。
    """
    from app.services.entry_day_checks import check_entry_bar, entry_window_days_for_rec
    from app.services.entry_window_state import open_days_after
    from app.services.outcome_simulation import _rec_is_st

    snap = json.loads(getattr(rec, "feature_snapshot_json", None) or "{}")
    window = entry_window_days_for_rec(rec)
    days = open_days_after(db, str(rec.trade_date), "2099-12-31")[:window]
    for day in days:
        bar = db.scalar(select(DailyKbar).where(DailyKbar.code == rec.code, DailyKbar.trade_date == day))
        if bar is None:
            return None, None, False
        chk = check_entry_bar(str(rec.code), bar, float(rec.recommend_price or 0), session_closed=True,
                              is_st=_rec_is_st(rec, snap))
        if not chk.blocked:
            return day, float(bar.open), True
    return None, None, len(days) >= window


def derived_execution_state(db: Session, rec: Any, fallback: Any | None) -> Any | None:
    """按新确认规则推出的执行资格；判定不了时退回原执行状态。"""
    day, _price, decidable = new_rule_confirmation(db, rec)
    if day:
        return CONFIRMED_STATE
    return NOT_CONFIRMED_STATE if decidable else fallback


__all__ = [
    "CONFIRMED_STATE",
    "RESETTLE_LABEL_SOURCE",
    "derived_execution_state",
    "new_rule_confirmation",
]
