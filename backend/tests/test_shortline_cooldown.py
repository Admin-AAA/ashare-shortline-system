"""§104 短线策略级权益曲线冷却：纯函数 + 接入行为（不连库）。"""
from __future__ import annotations

import json
from datetime import date, timedelta

import app.services.shortline_cooldown as SC


def _cal(n: int, start: str = "2026-01-05") -> list[str]:
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def test_inactive_until_60_trading_days_of_equity():
    cal = _cal(80)
    closed = [(cal[i], -1.0) for i in range(0, 50)]  # 持续亏损但只有 50 个交易日的权益
    st = SC.equity_ma_blocked(closed, cal, cal[49])
    assert st["active"] is False and st["ma_fast"] is None


def test_declining_equity_blocks_rising_does_not():
    cal = _cal(120)
    up = [(cal[i], 1.0) for i in range(100)]
    assert SC.equity_ma_blocked(up, cal, cal[99])["active"] is False
    down = [(cal[i], 1.0) for i in range(60)] + [(cal[i], -2.0) for i in range(60, 100)]
    st = SC.equity_ma_blocked(down, cal, cal[99])
    assert st["active"] is True and st["ma_fast"] < st["ma_slow"]


def test_trades_closing_after_signal_day_are_ignored():
    cal = _cal(120)
    base = [(cal[i], 1.0) for i in range(100)]
    future_loss = [(cal[105], -500.0)]
    a = SC.equity_ma_blocked(base, cal, cal[99])
    b = SC.equity_ma_blocked(base + future_loss, cal, cal[99])
    assert a == b


def test_apply_only_for_adopted_strategy_and_after_live_from(monkeypatch):
    cards = [{"code": "600000", "recommend_price": 10.0}]
    called = []
    monkeypatch.setattr(SC, "cooldown_state", lambda *a, **k: called.append(a) or {"active": True})
    monkeypatch.setattr(SC, "_is_live_formal", lambda db, run_id: True)
    out, st = SC.apply_live_cooldown(None, "r", "chen-xiaoqun-skill", "2026-10-20", cards)
    assert out == cards and st is None
    out, st = SC.apply_live_cooldown(None, "r", "sector-relative-strength-leader", "2026-10-16", cards)
    assert out == cards and st is None
    assert not called


def test_active_cooldown_blocks_and_records_shadow(monkeypatch, tmp_path):
    monkeypatch.setattr(SC, "SHADOW_DIR", tmp_path)
    monkeypatch.setattr(SC, "_is_live_formal", lambda db, run_id: True)
    monkeypatch.setattr(SC, "cooldown_state", lambda db, sid, t: {"active": True, "ma_fast": 1.0, "ma_slow": 2.0})
    cards = [{"code": "600000", "recommend_price": 10.0, "entry_mode": "target_touch", "final_score": 80}]
    out, st = SC.apply_live_cooldown(None, "r", "sector-relative-strength-leader", "2026-10-20", cards)
    assert out == [] and st["blocked"] == ["600000"]
    saved = json.loads((tmp_path / "sector-relative-strength-leader" / "2026-10-20.json").read_text())
    assert saved["picks"][0]["code"] == "600000" and saved["picks"][0]["signal_date"] == "2026-10-20"
    # 影子文件会被读回作为后续状态的影子单
    assert SC._shadow_files("sector-relative-strength-leader")[0]["code"] == "600000"


def test_inactive_or_error_passes_through(monkeypatch):
    cards = [{"code": "600000", "recommend_price": 10.0}]
    monkeypatch.setattr(SC, "_is_live_formal", lambda db, run_id: True)
    monkeypatch.setattr(SC, "cooldown_state", lambda db, sid, t: {"active": False})
    out, st = SC.apply_live_cooldown(None, "r", "sector-relative-strength-leader", "2026-10-20", cards)
    assert out == cards and st == {"active": False}

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(SC, "cooldown_state", boom)
    out, st = SC.apply_live_cooldown(None, "r", "sector-relative-strength-leader", "2026-10-20", cards)
    assert out == cards and st is None
    monkeypatch.setattr(SC, "_is_live_formal", lambda db, run_id: False)
    out, st = SC.apply_live_cooldown(None, "r", "sector-relative-strength-leader", "2026-10-20", cards)
    assert out == cards and st is None
