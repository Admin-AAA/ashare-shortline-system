"""板块相对强度龙头：结算按通过上线标准的回测规则入场（窗口内第一个可买日开盘买，不看碰买点）。"""
import json
from types import SimpleNamespace

from app.engine.execution_core import CausalEntryBar, CausalEntryConfig, resolve_causal_entry
from app.services.entry_day_checks import entry_window_days
from app.services.entry_policy import (
    BACKTEST_OPEN_WINDOW,
    CAUSAL_STRATEGY_ENTRY,
    entry_policy_for_mode,
    execution_policy_fingerprint_for_entry_policy,
    execution_policy_with_entry_mode,
)
from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY

SID = "sector-relative-strength-leader"


def _bar(day, o, low=None, close=None, block=None):
    return CausalEntryBar(code="300001", trade_date=day, open=o, low=low if low is not None else o - 0.5,
                          close=close if close is not None else o, block_reason=block)


def _cfg(policy, targets=()):
    return CausalEntryConfig(
        policy_id=policy.policy_id, price_source=policy.price_source, signal_date="2026-10-15",
        recommendation_price=10.0, intraday=False, target_prices=targets,
        max_entry_trade_days=policy.max_entry_trade_days, horizon_days=5,
        allow_target_touch=policy.allow_target_touch,
        fallback_to_open_when_targets_miss=policy.fallback_to_open_when_targets_miss,
        entry_slippage_bps=10.0, max_entry_gap_pct=6.0,
    )


def test_sector_leader_maps_to_backtest_open_window():
    assert entry_policy_for_mode("target_touch", legacy_strategy_id=SID) is BACKTEST_OPEN_WINDOW
    assert entry_policy_for_mode(None, legacy_strategy_id=SID) is BACKTEST_OPEN_WINDOW
    assert entry_window_days("target_touch", SID) == 2
    # 其它目标价策略不变
    assert entry_policy_for_mode("target_touch", legacy_strategy_id="other") is CAUSAL_STRATEGY_ENTRY


def test_backtest_path_policy_unchanged_for_sector_leader():
    """回测路径（execute_trade）用的执行合约不变：窗口 2 天、开盘买。"""
    policy = execution_policy_with_entry_mode(DEFAULT_EXECUTION_POLICY, "target_touch", legacy_strategy_id=SID)
    assert policy.entry == DEFAULT_EXECUTION_POLICY.entry
    fp_open = execution_policy_fingerprint_for_entry_policy(BACKTEST_OPEN_WINDOW.policy_id)
    fp_touch = execution_policy_fingerprint_for_entry_policy("causal_strategy_entry_v1")
    # 板块龙头新入场（开盘窗口）与旧碰买点规则指纹分开；都是合法 sha256，且确定性
    assert fp_touch == DEFAULT_EXECUTION_POLICY.fingerprint()
    assert fp_open != fp_touch and len(fp_open) == 64 and int(fp_open, 16) >= 0
    assert fp_open == execution_policy_fingerprint_for_entry_policy(BACKTEST_OPEN_WINDOW.policy_id)
    assert fp_open != execution_policy_fingerprint_for_entry_policy("next_trading_day_open_v1")


def test_open_window_buys_first_day_open_ignoring_targets():
    # 理想买点 9.5 当天被碰到，旧规则会按 9.5 成交；回测规则按开盘 10.1 买
    d = resolve_causal_entry((_bar("2026-10-16", 10.1, low=9.4),), _cfg(BACKTEST_OPEN_WINDOW, targets=(9.5,)))
    assert d.triggered and d.trade_date == "2026-10-16" and d.raw_price == 10.1
    old = resolve_causal_entry((_bar("2026-10-16", 10.1, low=9.4),), _cfg(CAUSAL_STRATEGY_ENTRY, targets=(9.5,)))
    assert old.raw_price == 9.5


def test_open_window_skips_gap_day_then_buys_day_two_like_backtest():
    d = resolve_causal_entry((_bar("2026-10-16", 10.7), _bar("2026-10-19", 10.3)), _cfg(BACKTEST_OPEN_WINDOW))
    assert d.triggered and d.trade_date == "2026-10-19" and d.raw_price == 10.3


def test_open_window_skips_blocked_day_and_reports_gap_when_all_gap():
    d = resolve_causal_entry((_bar("2026-10-16", 11.0, block="入场日一字涨停"), _bar("2026-10-19", 10.4)),
                             _cfg(BACKTEST_OPEN_WINDOW))
    assert d.trade_date == "2026-10-19"
    g = resolve_causal_entry((_bar("2026-10-16", 10.7), _bar("2026-10-19", 10.8)), _cfg(BACKTEST_OPEN_WINDOW))
    assert not g.triggered and g.reason == "gap_up_blocked"


def test_settlement_and_confirmation_agree_for_sector_leader(monkeypatch):
    """结算（_resolve_entry）与盘中确认（check_entry_bar）对同一根 K 给出同一结论、同一日同一价。"""
    from app.services import outcome_simulation as osim
    from app.services.entry_day_checks import check_entry_bar

    rec = SimpleNamespace(code="300001", trade_date="2026-10-15", recommend_price=10.0, bucket=SID,
                          recommended_at=None, feature_snapshot_json=json.dumps({"entry_mode": "target_touch"}))
    bar = SimpleNamespace(code="300001", trade_date="2026-10-16", open=10.2, high=10.6, low=9.4, close=10.5,
                          volume=1e5, amount=1e8, pct_chg=5.0)
    snapshot = {"entry_mode": "target_touch", "ideal_buy": 9.5}
    entry = osim._resolve_entry(rec, snapshot, [bar], 5)
    check = check_entry_bar("300001", {"open": 10.2, "high": 10.6, "low": 9.4, "close": 10.5, "pre_close": 10.0,
                                       "volume": 1e5, "amount": 1e8}, 10.0, session_closed=True)
    assert entry["triggered"] and not check.blocked
    assert entry["date"] == "2026-10-16" and entry["price"] == 10.2
