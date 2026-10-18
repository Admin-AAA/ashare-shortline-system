"""2026-10-19 settlement work: ex-right window root cause (300953), exit-price range validation,
settlement gate (exit bar must be post-close final), original preservation + rule_version."""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.models.tables import DailyKbar, Recommendation, RecommendationOutcome
from app.services import outcome_service as svc
from app.services.outcome_simulation import ExitPriceOutOfRangeError, _simulate_outcome, _validate_exit_price_in_bar

# 300953 real bars 2026-05-21..05-29 (ex-right on 05-28: adj_factor 1.4006 -> 1.9604)
_BARS = [
    ("2026-05-21", 243.28, 263.5, 242.55, 249.77, 3.0489, 1.4006),
    ("2026-05-22", 252.0, 260.35, 245.18, 256.03, 2.5063, 1.4006),
    ("2026-05-25", 254.78, 257.99, 247.6, 249.62, -2.5036, 1.4006),
    ("2026-05-26", 250.0, 253.68, 240.69, 245.0, -1.8508, 1.4006),
    ("2026-05-27", 244.0, 247.45, 229.0, 234.88, -4.1306, 1.4006),
    ("2026-05-28", 166.5, 168.0, 160.0, 163.84, -2.3658, 1.9604),
    ("2026-05-29", 163.85, 167.88, 144.99, 146.0, -10.8887, 1.9604),
]


def _bars(updated_at: datetime | None = None) -> list[DailyKbar]:
    return [
        DailyKbar(code="300953", trade_date=d, open=o, high=h, low=lo, close=c, pct_chg=p, adj_factor=a,
                  volume=1e6, amount=1e8, updated_at=updated_at or datetime(2026, 7, 29, 15, 0))
        for d, o, h, lo, c, p, a in _BARS
    ]


def _rec() -> Recommendation:
    snap = {"price": 249.77, "stop_loss": 238.53, "take_profit": 264.76, "current_price": 249.77,
            "recommend_price": 249.77}
    return Recommendation(recommendation_id="rec_t_300953", code="300953", trade_date="2026-05-21",
                          recommend_price=249.77, final_score=54.28, bucket="small-cap-thematic-momentum",
                          feature_snapshot_json=json.dumps(snap))


def test_ex_right_after_exit_no_longer_rewrites_exit_price():
    bars = _bars()
    sim = _simulate_outcome(_rec(), bars, [b.trade_date for b in bars], horizon_days=5)
    assert sim is not None and sim["entry_triggered"]
    exit_bar = next(b for b in bars if b.trade_date == sim["exit_date"])
    # stored (post-slippage) exit price stays within one slippage tick of the exit-day range
    assert exit_bar.low * 0.998 <= sim["exit_price"] <= exit_bar.high
    if sim["exit_date"] < "2026-05-28":
        expected = round((sim["exit_price"] / sim["entry_price"] - 1) * 100, 1)
        assert round(sim["executed_return"], 1) == pytest.approx(expected, abs=0.15)


def test_exit_price_validation_rejects_out_of_range():
    bars = _bars()
    with pytest.raises(ExitPriceOutOfRangeError):
        _validate_exit_price_in_bar(_rec(), bars, "2026-05-25", 226.1)
    _validate_exit_price_in_bar(_rec(), bars, "2026-05-25", 247.6)


def test_bar_finality_threshold():
    early = SimpleNamespace(trade_date="2026-09-21", updated_at=datetime(2026, 9, 21, 8, 25))  # 16:25 CST
    final = SimpleNamespace(trade_date="2026-09-21", updated_at=datetime(2026, 9, 21, 9, 36))  # 17:36 CST
    assert not svc._bar_is_final(early)
    assert svc._bar_is_final(final)
    assert svc._bar_is_final(SimpleNamespace(trade_date="2026-09-21", updated_at=None))


def test_settlement_gate_marks_provisional_until_exit_bar_final():
    sim = {"outcome_label": "success", "exit_date": "2026-09-21", "net_return_pct": 1.0,
           "canonical_exit_evidence": True, "intraday_exit_evidence": {"x": 1}}
    bars = [SimpleNamespace(trade_date="2026-09-18", updated_at=datetime(2026, 9, 18, 9, 36)),
            SimpleNamespace(trade_date="2026-09-21", updated_at=datetime(2026, 9, 21, 8, 25))]
    gated = svc._apply_settlement_gate(sim, bars)
    assert gated["outcome_label"] == "provisional" and gated["net_return_pct"] is None
    assert gated["canonical_exit_evidence"] is False and gated["intraday_exit_evidence"] is None
    bars[1].updated_at = datetime(2026, 9, 21, 9, 40)
    assert svc._apply_settlement_gate(sim, bars)["outcome_label"] == "success"
    # missing exit bar -> provisional
    assert svc._apply_settlement_gate(sim, bars[:1])["outcome_label"] == "provisional"


def test_preserve_original_is_write_once():
    o = RecommendationOutcome(recommendation_id="r", outcome_label="success", exit_date="2026-09-22",
                              exit_price=24.46, exit_reason="horizon_close", executed_return=14.9)
    svc._preserve_original_settlement(o)
    assert (o.original_outcome_label, o.original_executed_return, o.original_rule_version) == ("success", 14.9, "legacy")
    o.executed_return = 30.44
    o.rule_version = "other"
    svc._preserve_original_settlement(o)
    assert o.original_executed_return == 14.9


def test_backfill_bump_never_makes_settled_bar_provisional():
    # backfills now bump updated_at to "now": any time after trade_date 17:30 CST stays final
    for ts in (datetime(2026, 9, 21, 9, 30), datetime(2026, 10, 19, 1, 0), datetime(2030, 1, 1)):
        assert svc._bar_is_final(SimpleNamespace(trade_date="2026-09-21", updated_at=ts))
