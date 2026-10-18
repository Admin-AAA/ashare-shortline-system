"""逐策略均线破位出场（Sprint3 §71）：持仓中某日收盘 < MAk（日K 表 ma5/ma10）即当日收盘离场。

做法（与规范执行核同口径、无近似）：先按原出场模拟得到入场日/离场日；若 (入场日, 原离场日) 之间
存在可卖（非一字跌停、有量）且收盘 < MAk 的交易日 t，则把持仓 K 线截到 t 重新模拟——
规范核在持仓窗末日按收盘离场（含滑点/费用），离场原因改记 `ma_break_close`。只会提前离场。
策略在 strategy.yaml 声明 `ma_break_exit: {window: 5|10}`；未声明的策略完全不受影响。
"""
from __future__ import annotations

import dataclasses as dc
from functools import lru_cache
from typing import Any, Callable, Mapping, Sequence

import yaml

from app.engine.board_rules import is_one_word_limit_down_bar
from app.services.strategy_exit_overrides import SKILLS_DIR

MA_BREAK_REASON = "ma_break_close"


@lru_cache(maxsize=1)
def load_ma_break_windows() -> dict[str, int]:
    out: dict[str, int] = {}
    for path in sorted(SKILLS_DIR.glob("*/strategy.yaml")):
        try:
            contract = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 - 坏 yaml 由 skill_loader 报错
            continue
        raw = contract.get("ma_break_exit") or {}
        if isinstance(raw, Mapping) and int(raw.get("window") or 0) in (5, 10):
            out[str(contract.get("id") or path.parent.name)] = int(raw["window"])
    return out


def ma_break_window_for(strategy_id: str | None) -> int:
    return load_ma_break_windows().get(str(strategy_id), 0) if strategy_id else 0


def first_break_date(
    code: str,
    rows: Sequence[Any],
    window: int,
    after_date: str,
    before_date: str,
) -> str | None:
    """rows：带 trade_date/close/ma5/ma10/open/high/low/pct_chg/volume 的日 K（升序）。"""
    attr = f"ma{window}"
    closes: list[float] = []
    for r in rows:
        d = str(r.trade_date)
        close = float(getattr(r, "close", 0) or 0)
        closes.append(close)
        if d <= after_date:
            continue
        if d >= before_date:
            return None
        ma = float(getattr(r, attr, 0) or 0)
        if ma <= 0:
            # 2026-10-19：库里 ma 为 0/NULL 时按 K 线现算（未复权收盘简单均值，与 market_data 兜底同口径）；
            # 窗口不足（rows 内可用收盘 < window）则跳过该日。
            window_closes = closes[-window:]
            if len(window_closes) == window and all(c > 0 for c in window_closes):
                ma = sum(window_closes) / window
        if close <= 0 or ma <= 0 or float(getattr(r, "volume", 0) or 0) <= 0:
            continue
        if is_one_word_limit_down_bar(code, r):
            continue
        if close < ma:
            return d
    return None


# ---- 回测侧：按股票惰性读日K（进程内缓存） ----
_MA_ROW_CACHE: dict[str, tuple] = {}


def _ma_rows(code: str) -> tuple:
    rows = _MA_ROW_CACHE.get(code)
    if rows is None:
        from sqlalchemy import select

        from app.db.session import SessionLocal
        from app.models.tables import DailyKbar

        with SessionLocal() as db:
            rows = tuple(db.execute(
                select(DailyKbar.trade_date, DailyKbar.open, DailyKbar.high, DailyKbar.low, DailyKbar.close,
                       DailyKbar.pct_chg, DailyKbar.volume, DailyKbar.ma5, DailyKbar.ma10)
                .where(DailyKbar.code == code).order_by(DailyKbar.trade_date)
            ).all())
        if len(_MA_ROW_CACHE) > 20000:
            _MA_ROW_CACHE.clear()
        _MA_ROW_CACHE[code] = rows
    return rows


def apply_ma_break_backtest(
    trade: Any,
    bars: Sequence[Any],
    window: int,
    resimulate: Callable[[tuple], Any],
    rows_loader: Callable[[str], Sequence[Any]] = _ma_rows,
) -> Any:
    """trade：simulate() 的 Trade；bars：原 outcome bars；resimulate(截断后的 bars)->Trade。"""
    if not window or trade is None or not getattr(trade, "executed", False):
        return trade
    if not trade.entry_date or not trade.exit_date:
        return trade
    t = first_break_date(trade.code, rows_loader(trade.code), window, str(trade.entry_date), str(trade.exit_date))
    if t is None:
        return trade
    cut = tuple(b for b in bars if str(b.trade_date) <= t)
    new = resimulate(cut)
    if new is None or not getattr(new, "executed", False) or str(new.exit_date) != t:
        return trade  # 截断改变了入场/更早离场以外的结果 → 保守不改
    if new.exit_reason and str(new.exit_reason).startswith("horizon_close"):
        new = dc.replace(new, exit_reason=MA_BREAK_REASON)
    return new


__all__ = ["MA_BREAK_REASON", "apply_ma_break_backtest", "first_break_date", "load_ma_break_windows", "ma_break_window_for"]
