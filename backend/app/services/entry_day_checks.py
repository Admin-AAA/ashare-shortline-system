"""盘中买入确认的「入场日检查」：与回测买入规则一一对应，不重判信号日的形态与筛选条件。

回测（``app.engine.execution_core._resolve_entry`` + 默认 execution policy）只用入场日行情决定能否
按开盘价买入：

==================  ======================================  ===========================================
回测规则             回测做法                                  盘中确认（本模块）
==================  ======================================  ===========================================
入场窗口 2 个交易日   ``entry_lookahead_days``；首个可买日即买入   推荐后第 1 个交易日确认；仅当第 1 日被下面三条
                                                             之一挡住时，第 2 个交易日再确认；超出即过期
无成交 / 价格无效     ``skip_untradable_entry_bars``：跳过该日     开盘价/成交量/成交额无效 → 该日不买
一字涨停             ``block_one_word_limit_up_entry``：跳过该日  开盘封涨停且尚未开板 → 继续观察（收盘前不定论）；
                                                             已开板 → 按回测口径当日可买
跳空高开 > 6%        ``max_entry_gap_pct``：开盘价相对推荐价 >6%   开盘价相对推荐价 > 6% → 该日不买
                     跳过该日
==================  ======================================  ===========================================

回测**没有**「入场前已跌破推荐止损位就不买」这条：回测的止损价按实际买入价 ×(1−5.5%) 设定，
低开照样按开盘价买入。因此盘中确认也不再因跌破推荐止损位取消买入（止损只用于持仓提示）。
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from app.engine.board_rules import is_one_word_limit_up_values, previous_close_from_price
from app.engine.execution_constraints import no_liquidity_reason_from_bar
from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY

ENTRY_WINDOW_TRADE_DAYS = int(DEFAULT_EXECUTION_POLICY.entry_lookahead_days)
MAX_ENTRY_GAP_PCT = float(DEFAULT_EXECUTION_POLICY.max_entry_gap_pct or 0.0)

# 规则 ID（写进 condition_summary.code，便于复盘统计）
RULE_UNTRADABLE = "entry_untradable"
RULE_ONE_WORD = "entry_one_word_limit_up"
RULE_GAP_UP = "entry_gap_up"
RULE_PASS = "entry_day_checks_passed"


@dataclass(frozen=True)
class EntryDayCheck:
    """入场日检查结果。blocked=False 表示按回测口径当日可按开盘价买入。"""

    blocked: bool
    rule_id: str
    reason: str
    # 一字涨停在收盘前还可能开板：盘中不定论
    inconclusive: bool = False
    observed: dict[str, Any] | None = None


def _f(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def check_entry_bar(
    code: str,
    bar: dict[str, Any] | Any,
    recommend_price: float,
    *,
    session_closed: bool,
) -> EntryDayCheck:
    """按回测买入规则检查入场日这一根 K（盘中为开盘至今的实时 K）。

    bar 需含 open/high/low/close/volume/amount，以及 pre_close（或 pct_chg，用于反推前收）。
    """
    get = (lambda k: bar.get(k)) if isinstance(bar, dict) else (lambda k: getattr(bar, k, None))
    open_price, high, low, close = _f(get("open")), _f(get("high")), _f(get("low")), _f(get("close"))
    volume = _f(get("volume") if get("volume") is not None else get("vol"))
    amount = _f(get("amount"))
    pre_close = _f(get("pre_close"))
    if pre_close <= 0 and close > 0:
        pre_close = previous_close_from_price(close, get("pct_chg") or 0)
    gap_pct = round((open_price / recommend_price - 1) * 100, 2) if recommend_price > 0 and open_price > 0 else None
    observed = {"open": open_price, "high": high, "low": low, "close": close, "pre_close": pre_close,
                "recommend_price": recommend_price, "gap_pct": gap_pct, "max_entry_gap_pct": MAX_ENTRY_GAP_PCT}

    shim = SimpleNamespace(open=open_price, close=close, volume=volume, amount=amount)
    if no_liquidity_reason_from_bar(shim, side="entry"):
        return EntryDayCheck(True, RULE_UNTRADABLE, "入场日无成交量或价格无效，回测口径当日不买", observed=observed)
    if DEFAULT_EXECUTION_POLICY.board_limits.block_one_word_limit_up_entry and is_one_word_limit_up_values(
        code, prev_close=pre_close, open_price=open_price, high=high, low=low, close=close,
    ):
        if session_closed:
            return EntryDayCheck(True, RULE_ONE_WORD, "入场日一字涨停，回测口径买不进", observed=observed)
        return EntryDayCheck(True, RULE_ONE_WORD, "开盘封涨停尚未开板，回测口径一字板买不进；开板前不确认", inconclusive=True, observed=observed)
    if gap_pct is not None and MAX_ENTRY_GAP_PCT > 0 and gap_pct > MAX_ENTRY_GAP_PCT:
        return EntryDayCheck(True, RULE_GAP_UP, f"开盘价高于推荐价 {gap_pct:.2f}%（> {MAX_ENTRY_GAP_PCT:.0f}%），回测口径当日不买", observed=observed)
    gap_text = f"{gap_pct:+.2f}%" if gap_pct is not None else "—"
    return EntryDayCheck(False, RULE_PASS, f"入场日检查通过：非一字涨停、可成交、开盘相对推荐价 {gap_text}（≤{MAX_ENTRY_GAP_PCT:.0f}%）", observed=observed)


def first_day_blocked(code: str, day_one_bar: Any, recommend_price: float) -> bool:
    """推荐后第 1 个交易日（已收盘的日 K）是否被回测买入规则挡住——决定第 2 个交易日还能不能买。"""
    if day_one_bar is None:
        return True  # 停牌无 K：回测跳过该日
    return check_entry_bar(code, day_one_bar, recommend_price, session_closed=True).blocked


__all__ = [
    "ENTRY_WINDOW_TRADE_DAYS",
    "MAX_ENTRY_GAP_PCT",
    "EntryDayCheck",
    "check_entry_bar",
    "first_day_blocked",
]
