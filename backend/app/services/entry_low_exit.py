"""逐策略破买入日最低价出场（Sprint4 §185）：持仓中某日收盘 < 买入日最低价即当日收盘离场。

做法（与 ma_break_exit / 规范执行核同口径）：先按原出场模拟得到入场日/离场日；若 (入场日, 原离场日)
之间存在可卖（非一字跌停、有量）且收盘 < 买入日最低价的交易日 t，则把持仓 K 线截到 t 重新模拟——
规范核在持仓窗末日按收盘离场（含滑点/费用），离场原因改记 `entry_low_close`。只会提前离场。
策略在 strategy.yaml 声明 `entry_low_exit: {mode: close}`；未声明的策略完全不受影响。
预注册与回测：docs/strategy-research.md §185，EL_C 版本按 8 条标准通过后上线。
"""
from __future__ import annotations

import dataclasses as dc
from functools import lru_cache
from typing import Any, Callable, Mapping, Sequence

import yaml

from app.engine.board_rules import is_one_word_limit_down_bar
from app.services.strategy_exit_overrides import SKILLS_DIR

ENTRY_LOW_REASON = "entry_low_close"


@lru_cache(maxsize=1)
def load_entry_low_modes() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(SKILLS_DIR.glob("*/strategy.yaml")):
        try:
            contract = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        raw = contract.get("entry_low_exit") or {}
        if isinstance(raw, Mapping) and str(raw.get("mode") or "").strip().lower() == "close":
            out[str(contract.get("id") or path.parent.name)] = "close"
    return out


def entry_low_enabled_for(strategy_id: str | None) -> bool:
    return bool(strategy_id) and str(strategy_id) in load_entry_low_modes()


def first_entry_low_break(
    code: str,
    bars: Sequence[Any],
    after_date: str,
    before_date: str,
) -> str | None:
    """bars：带 trade_date/open/high/low/close/volume 的日 K（升序，含入场日）。
    after_date=入场日（当日不算）；before_date=原离场日（及之后不算）。
    """
    entry_low = None
    for r in bars:
        d = str(r.trade_date)
        if d == after_date:
            entry_low = float(getattr(r, "low", 0) or 0)
            break
    if entry_low is None or entry_low <= 0:
        return None
    for r in bars:
        d = str(r.trade_date)
        if d <= after_date:
            continue
        if d >= before_date:
            return None
        close = float(getattr(r, "close", 0) or 0)
        if close <= 0 or float(getattr(r, "volume", 0) or 0) <= 0:
            continue
        if is_one_word_limit_down_bar(code, r):
            continue
        if close < entry_low:
            return d
    return None


def apply_entry_low_backtest(
    trade: Any,
    bars: Sequence[Any],
    enabled: bool,
    resimulate: Callable[[tuple], Any],
) -> Any:
    """trade：simulate() 的 Trade；bars：原 outcome bars；resimulate(截断后的 bars)->Trade。"""
    if not enabled or trade is None or not getattr(trade, "executed", False):
        return trade
    if not trade.entry_date or not trade.exit_date:
        return trade
    t = first_entry_low_break(str(trade.code), bars, str(trade.entry_date), str(trade.exit_date))
    if t is None:
        return trade
    cut = tuple(b for b in bars if str(b.trade_date) <= t)
    new = resimulate(cut)
    if new is None or not getattr(new, "executed", False) or str(new.exit_date) != t:
        return trade
    if new.exit_reason and str(new.exit_reason).startswith("horizon_close"):
        new = dc.replace(new, exit_reason=ENTRY_LOW_REASON)
    return new


__all__ = [
    "ENTRY_LOW_REASON",
    "apply_entry_low_backtest",
    "entry_low_enabled_for",
    "first_entry_low_break",
    "load_entry_low_modes",
]
