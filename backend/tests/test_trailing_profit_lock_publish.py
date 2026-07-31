"""回归测试：锁定 trailing profit_floor 锁利比例发布值 (0.50 -> 0.30)。

self-improve si-trailing-lock-030 决策：
  clean≥72 20cm-only 配对回测 (n=33, 同口径重放, 扣 0.07%/笔, horizon=5) 显示把
  TRAILING_STOP_PROFIT_LOCK_RATIO 从 0.50 降到 0.30 后，锁利比例放宽让更多交易
  穿越正常日内回调抵达止盈段 (avg_net +0.113pp, PF +0.057, WR/max_loss 持平)。
  满足 L-010 整体改善条件 -> PRODUCTION。

本测试（DB-free）锁定三条不变式，防止未来静默回退到 0.50 或破坏发布机制：
  1. 生产常量 == 0.30（锁定发布值）。
  2. 提升 ratio 在「峰值后回撤」路径上锁住更高比例的浮盈（捕获更多峰值），
     即 ratio=0.30 的出场收益严格低于 ratio=0.50，且均为 trailing_stop_hit。
  3. 生产默认 _resolve_exit 等同 _resolve_exit_causal(profit_lock_ratio=常量)
     （身份自检，保证 LIVE 结算与回测重放同源）。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.outcome_simulation import (
    TRAILING_STOP_PROFIT_LOCK_RATIO,
    _resolve_exit,
    _resolve_exit_causal,
)

PUBLISHED_RATIO = 0.30
ENTRY_PRICE = 100.0
STOP_LOSS_PRICE = ENTRY_PRICE * (1 - 0.055)  # 生产固定止损约 -5.5%
TAKE_PROFIT_PRICE = 200.0  # 远高于测试窗口，避免止盈路径干扰


def _bar(open_, high, low, close, trade_date):
    return SimpleNamespace(
        open=open_, high=high, low=low, close=close,
        volume=1_000_000, amount=100_000_000, trade_date=trade_date,
    )


def _peak_then_retrace_bars():
    """入场日平开 -> 次日冲高 +10%（激活 trailing）-> 第三日以峰值开盘后回落至 +3%。

    open 设为 110（高于两档锁利价 105/109），避免触发更保守的 trailing_stop_gap，
    使两档都走 trailing_stop_hit，退出价即锁利价，数值可精确锁定。
    """
    entry = _bar(100, 100, 100, 100, "2026-01-01")
    peak = _bar(100, 110, 100, 108, "2026-01-02")
    retrace = _bar(110, 110, 103, 103, "2026-01-03")
    return [entry, peak, retrace]


def _exit_return(ratio: float) -> tuple[float, str]:
    bars = _peak_then_retrace_bars()
    px, _, reason = _resolve_exit_causal(
        bars, STOP_LOSS_PRICE, TAKE_PROFIT_PRICE,
        code="TEST", entry_price=ENTRY_PRICE,
        dynamic_trailing=True, trailing_policy="profit_floor",
        profit_lock_ratio=ratio,
    )
    return (px / ENTRY_PRICE - 1) * 100.0, reason


def test_published_ratio_is_locked():
    # 锁定发布值，禁止静默回退到 0.50
    assert TRAILING_STOP_PROFIT_LOCK_RATIO == PUBLISHED_RATIO


def test_higher_ratio_captures_more_peak():
    # 峰值后回撤路径：ratio=0.90 锁利价更高 -> 出场收益严格高于 0.50
    ret_low, reason_low = _exit_return(0.50)
    ret_high, reason_high = _exit_return(0.30)
    assert reason_low == "trailing_stop_hit"
    assert reason_high == "trailing_stop_hit"
    # 0.50 锁 50% 峰值 -> 出场 +5.0%；0.30 锁 30% 峰值 -> 出场 +3.0%（给足空间穿越波动）
    # 注意：30% of 10% = 3%, profit_floor = max(min_lock(0.3), 3.0) = 0.3+3=3.3% → 103
    # 实际：low=103 < 103.3 → trailing_stop_hit at 103.3
    # ret_low (0.50) = 5.0%, ret_high (0.30) → profit_floor = max(0.3, 10*0.3)=3.0→103
    # But min_lock=0.3 gives max(0.3, 3.0)=3.0 → same
    # Actually the test bars are: D1=100, D2 open=100 high=110 low=100 close=108, D3 open=110 high=110 low=103 close=103
    # D2 hwm=10%, activate trailing. profit_floor 0.50: 10*0.5=5% → prot=105. D3 low=103 → trailing_stop_hit at 105=+5%
    # profit_floor 0.30: 10*0.3=3% → max(0.3, 3.0)=3.0 → prot=103. D3 low=103 ≤103 → trailing_stop_hit at 103=+3%
    assert abs(ret_low - 5.0) < 1e-9
    assert abs(ret_high - 3.0) < 1e-9
    assert ret_high < ret_low
    # 验证：0.30 锁利比例更低 → 留更多空间给上涨，锁住的浮盈更少但整体 avg_net 更高


def test_default_resolver_matches_published_constant():
    # 生产默认 _resolve_exit 必须等于 _resolve_exit_causal(常量)，保证 LIVE 与回测同源
    bars = _peak_then_retrace_bars()
    px_default, _, reason_default = _resolve_exit(
        bars, STOP_LOSS_PRICE, TAKE_PROFIT_PRICE,
        code="TEST", entry_price=ENTRY_PRICE,
    )
    px_explicit, _, reason_explicit = _resolve_exit_causal(
        bars, STOP_LOSS_PRICE, TAKE_PROFIT_PRICE,
        code="TEST", entry_price=ENTRY_PRICE,
        dynamic_trailing=True, trailing_policy="profit_floor",
        profit_lock_ratio=TRAILING_STOP_PROFIT_LOCK_RATIO,
    )
    assert reason_default == reason_explicit == "trailing_stop_hit"
    assert abs(px_default - px_explicit) < 1e-9
