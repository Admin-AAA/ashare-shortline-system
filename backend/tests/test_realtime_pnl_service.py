"""realtime_pnl_service 单测：mock rt_k + _resolve_entry，验证实盘浮盈计算与降级。"""

from __future__ import annotations

import pytest

from app.models.tables import DailyKbar
from app.services import realtime_pnl_service as svc


class _Rec:
    """最小 Recommendation 替身，承载 _resolve_entry 所需字段。"""

    def __init__(self, code, trade_date="2026-07-24", recommend_price=18.0, fs=None):
        self.recommendation_id = f"rec_{code}"
        self.code = code
        self.trade_date = trade_date
        self.recommend_price = recommend_price
        self.recommended_at = None  # 盘后处理
        import json

        self.feature_snapshot_json = json.dumps(fs or {"ideal_buy": 18.23, "secondary_buy": 17.95})


def _quote(open_p, high, low, close, vol=1000, amount=1e6):
    return {"open": open_p, "high": high, "low": low, "close": close,
            "pre_close": close, "vol": vol, "amount": amount, "name": "测试"}


def test_holding_pnl_buy_target_touched(monkeypatch):
    # low 触及买点 18.23 → 入场价 min(open,18.23)，现价低于入场价 → 浮亏
    monkeypatch.setattr(svc, "_fetch_rtk_quotes", lambda codes: {"600539": _quote(20.36, 20.36, 16.88, 17.78)})
    rec = _Rec("600539")
    out = svc._compute_one(rec, svc._fetch_rtk_quotes([])["600539"], signal_bar=None)
    assert out["status"] == "holding"
    assert out["entry_price"] == pytest.approx(18.23)
    assert out["pnl_pct"] == pytest.approx(round((17.78 - 18.23) / 18.23 * 100, 2))


def test_holding_pnl_profit(monkeypatch):
    # 买点命中，现价高于入场价 → 浮盈
    monkeypatch.setattr(svc, "_fetch_rtk_quotes", lambda codes: {"605028": _quote(25.28, 28.0, 25.28, 27.7)})
    rec = _Rec("605028", fs={"ideal_buy": 26.79, "secondary_buy": 26.38})
    out = svc._compute_one(rec, svc._fetch_rtk_quotes([])["605028"], signal_bar=None)
    assert out["status"] == "holding"
    assert out["pnl_pct"] > 0


def test_unavailable_when_no_quote():
    rec = _Rec("000011")
    out = svc._compute_one(rec, None, signal_bar=None)
    assert out["status"] == "unavailable"
    assert "无数据" in out["reason"] or "不可用" in out["reason"]


def test_unavailable_when_quote_close_zero():
    rec = _Rec("000011")
    out = svc._compute_one(rec, _quote(8.0, 8.5, 7.9, 0), signal_bar=None)
    assert out["status"] == "unavailable"


def test_watching_when_target_not_touched(monkeypatch):
    # low 远高于买点 → 买点未触发 → watching + 距买点
    rec = _Rec("000011", fs={"ideal_buy": 7.0, "secondary_buy": 6.8})
    # open/low 都高于 7.0，且无买点命中 → _resolve_entry 走开盘回退仍会 triggered
    # 改为 low 高于买点但 open 有效 → 应触发开盘回退 holding；真正的 watching 需要
    # _resolve_entry 返回 triggered=False。用 gap 过大触发未触发路径更稳。
    # 这里直接验证 watching 分支结构：构造一个 triggered=False 的 _resolve_entry
    monkeypatch.setattr(svc, "_resolve_entry", lambda *a, **k: {"triggered": False, "reason": "买点未触发"})
    out = svc._compute_one(rec, _quote(8.0, 8.5, 7.9, 8.15), signal_bar=None)
    assert out["status"] == "watching"
    assert out["now_price"] == pytest.approx(8.15)
    assert out["nearest_buy_target"] == pytest.approx(7.0)
    assert out["distance_to_target_pct"] is not None


def test_signal_bar_used_for_intraday_judgement():
    # 提供 signal_bar（trade_date 当日日K）时 _resolve_entry 能区分盘中/盘后
    rec = _Rec("601606")
    sig = DailyKbar(code="601606", trade_date="2026-07-24", open=30.0, high=31.0, low=29.5,
                    close=30.5, volume=1000, amount=1e6)
    q = _quote(32.26, 35.5, 32.25, 35.26)
    out = svc._compute_one(rec, q, signal_bar=sig)
    assert out["status"] == "holding"
    assert out["entry_date"] == "2026-07-27"
