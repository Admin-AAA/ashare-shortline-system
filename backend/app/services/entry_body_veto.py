"""逐策略「入场日开盘低于信号日实体中点不买」（§354 OS_INV_BODY_K 预注册通过，边际改进）。

规则：T+1 开盘价 < (信号日日K开盘 + 信号日日K收盘)/2 → 当日不买（次日开盘类 1 天窗口，即放弃该推荐）。
信号日 OHLC 取 DailyKbar（研究口径 kbar，不用卡片快照，卡片 OHLC 在历史面板为空）。
策略在 strategy.yaml 声明 `entry_body_veto: {mode: signal_body_mid}`；未声明的策略完全不受影响。

状态：本模块提供规则加载与纯函数判定，并接入 entry_day_checks.check_entry_bar（传 signal_bar 时生效）。
回测核（execution_core._resolve_entry）与实盘结算（outcome_simulation）的调用点需在评审时确认后接线——见 PR 说明。
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Mapping

import yaml

from app.services.strategy_exit_overrides import SKILLS_DIR

logger = logging.getLogger(__name__)

RULE_BODY_VETO = "entry_below_signal_body_mid"


@lru_cache(maxsize=1)
def load_body_veto_modes() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(SKILLS_DIR.glob("*/strategy.yaml")):
        try:
            contract = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            logger.warning("entry_body_veto: 读取 %s 失败，该策略入场规则未加载", path, exc_info=True)
            continue
        raw = contract.get("entry_body_veto") or {}
        if isinstance(raw, Mapping) and str(raw.get("mode") or "").strip().lower() == "signal_body_mid":
            out[str(contract.get("id") or path.parent.name)] = "signal_body_mid"
    return out


def body_veto_enabled_for(strategy_id: str | None) -> bool:
    return bool(strategy_id) and str(strategy_id) in load_body_veto_modes()


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def signal_body_mid(signal_bar: Mapping[str, Any] | Any) -> float | None:
    get = (lambda k: signal_bar.get(k)) if isinstance(signal_bar, Mapping) else (lambda k: getattr(signal_bar, k, None))
    o, c = _f(get("open")), _f(get("close"))
    if o <= 0 or c <= 0:
        return None
    return (o + c) / 2.0


def body_veto_blocked(signal_bar: Any, entry_open: float) -> tuple[bool, float | None]:
    """返回 (是否挡住, 实体中点)。信号日 K 缺失/无效时不挡（与研究口径一致：缺数据不跳过）。"""
    mid = signal_body_mid(signal_bar) if signal_bar is not None else None
    if mid is None or entry_open is None or entry_open <= 0:
        return False, mid
    return entry_open < mid, mid


__all__ = ["RULE_BODY_VETO", "load_body_veto_modes", "body_veto_enabled_for", "signal_body_mid", "body_veto_blocked"]
