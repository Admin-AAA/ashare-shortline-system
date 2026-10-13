"""组合层回撤控制（纯函数，无 IO）。

两条规则，都只用决策时刻（信号日 17:35）已知的信息：

1. 弱市限额：信号日 regime 属于 ``weak_regimes`` 时，当日最多出 ``weak_max_tickets`` 张（按分数取前 N）。
2. 亏损熔断：只统计**已结算**的历史推荐（信号日早于当前信号日至少 ``settle_lag_days`` 个交易日，
   保证持有期已结束），最近 ``lookback_days`` 个出票日的逐日均值累计 ≤ ``breaker_threshold``
   时，暂停出票 ``pause_days`` 个交易日。

参数只能在 hold-out 之前的训练段上选择（见 docs/holdout-protocol.md）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence


@dataclass(frozen=True)
class DrawdownControlConfig:
    enabled: bool = False
    weak_regimes: tuple[str, ...] = ("弱势",)
    weak_max_tickets: int | None = None  # None = 不限
    breaker_threshold: float | None = None  # 逐日均值累计（%），None = 不启用熔断
    lookback_days: int = 10
    settle_lag_days: int = 6
    pause_days: int = 3


@dataclass
class DrawdownControlState:
    """按交易日顺序推进的状态。history: [(signal_date_index, daily_mean_net)]"""

    history: list[tuple[int, float]] = field(default_factory=list)
    paused_until_index: int = -1


def breaker_tripped(state: DrawdownControlState, day_index: int, cfg: DrawdownControlConfig) -> bool:
    if cfg.breaker_threshold is None:
        return False
    settled = [v for i, v in state.history if i <= day_index - cfg.settle_lag_days]
    window = settled[-cfg.lookback_days:]
    if len(window) < max(3, cfg.lookback_days // 2):
        return False
    return sum(window) <= cfg.breaker_threshold


def apply_drawdown_control(
    picks: Sequence[str],
    *,
    regime: str | None,
    day_index: int,
    state: DrawdownControlState,
    cfg: DrawdownControlConfig,
) -> tuple[list[str], str | None]:
    """返回 (保留的代码, 拦截原因)。picks 须已按分数从高到低排序。"""
    if not cfg.enabled or not picks:
        return list(picks), None
    if day_index <= state.paused_until_index:
        return [], "drawdown_breaker_paused"
    if breaker_tripped(state, day_index, cfg):
        state.paused_until_index = day_index + cfg.pause_days - 1
        return [], "drawdown_breaker_tripped"
    if cfg.weak_max_tickets is not None and regime in cfg.weak_regimes:
        kept = list(picks)[: cfg.weak_max_tickets]
        return kept, ("weak_market_throttle" if len(kept) < len(picks) else None)
    return list(picks), None


def record_day(state: DrawdownControlState, day_index: int, nets: Iterable[float | None]) -> None:
    vals = [float(n) for n in nets if n is not None]
    if vals:
        state.history.append((day_index, sum(vals) / len(vals)))
