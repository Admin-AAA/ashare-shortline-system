from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.outcome_service import (
    _board_and_net_breakdown,
    _metrics_by_score_line,
    _metrics_by_setup,
    _metrics_by_strategy,
    _overview_metrics,
    _precision_at_k,
    is_accurate_recommendation,
)


class _Rows:
    def __init__(self, rows: list[SimpleNamespace]):
        self._rows = rows

    def all(self) -> list[SimpleNamespace]:
        return self._rows


def _row(
    *,
    rec_id: str,
    outcome_label: str,
    entry_triggered: bool,
    executed_return: float,
    score: float = 80.0,
    setup_type: str = "突破",
    skill_breakdown_json: str = "[]",
) -> SimpleNamespace:
    return SimpleNamespace(
        recommendation_id=rec_id,
        run_id="run_accuracy_policy",
        code=rec_id[-6:].rjust(6, "0"),
        name=rec_id,
        final_score=score,
        setup_type=setup_type,
        skill_breakdown_json=skill_breakdown_json,
        trade_date="2026-05-12",
        market_context_json="{}",
        outcome_label=outcome_label,
        entry_triggered=entry_triggered,
        executed_return=executed_return,
        weighted_return=executed_return,
        close_return=executed_return,
        achievable_return=executed_return,
        max_return=max(executed_return, 0.0),
        min_return=min(executed_return, 0.0),
        max_drawdown=min(executed_return, 0.0),
        holding_days=1 if entry_triggered else 0,
        outcome_reason=None if executed_return > 0 else "成本后净收益不为正",
        entry_date="2026-05-13" if entry_triggered else None,
        exit_date="2026-05-14" if entry_triggered else None,
        label_source="manual",
    )


def test_accurate_recommendation_requires_executed_positive_net_return():
    assert is_accurate_recommendation(
        SimpleNamespace(entry_triggered=True, outcome_label="success", executed_return=1.2)
    )
    assert not is_accurate_recommendation(
        SimpleNamespace(entry_triggered=True, outcome_label="success", executed_return=-0.2)
    )
    assert not is_accurate_recommendation(
        SimpleNamespace(entry_triggered=False, outcome_label="success", executed_return=3.0)
    )


def test_overview_metrics_do_not_trust_success_label_when_net_return_is_negative(monkeypatch):
    rows = [
        _row(rec_id="rec_win", outcome_label="success", entry_triggered=True, executed_return=2.0),
        _row(rec_id="rec_label_success_loss", outcome_label="success", entry_triggered=True, executed_return=-0.2),
        _row(rec_id="rec_loss", outcome_label="failed", entry_triggered=True, executed_return=-1.5),
    ]
    monkeypatch.setattr("app.services.outcome_service._scoped_recommendation_rows", lambda _db, _s=None, _e=None, **_k: rows)
    monkeypatch.setattr("app.services.benchmark_service.get_benchmark_close_map", lambda _db, _dates: {})
    monkeypatch.setattr("app.services.benchmark_service.compute_benchmark_return", lambda _close_map, _entry, _exit: 0.0)

    metrics = _overview_metrics(MagicMock())

    assert metrics["labeled_count"] == 3
    assert metrics["triggered_count"] == 3
    assert metrics["success_count"] == 1
    assert metrics["failed_count"] == 2
    assert metrics["recommendation_accuracy"] == 0.333
    assert metrics["trade_accuracy"] == 0.333
    assert metrics["accuracy_basis"] == "labeled_recommendations"
    assert metrics["accuracy_gate_summary"] == {
        "total_recommendations": 3,
        "signal_valid_count": 3,
        "signal_invalid_count": 0,
        "executable_count": 3,
        "unexecutable_count": 0,
        "net_positive_count": 1,
        "net_non_positive_count": 2,
        "manual_override_count": 0,
        "legacy_accuracy_label_mismatch_count": 0,
    }
    assert metrics["accuracy_policy"]["matches_reported_accuracy"] is True
    assert metrics["accuracy_policy"]["failure_breakdown"] == [
        {"gate": "net_return_positive", "label": "成本后净收益不为正", "count": 2}
    ]


def test_overview_and_d_net_use_identical_official_winrate_scope(monkeypatch):
    rows = [
        _row(rec_id="rec_win", outcome_label="success", entry_triggered=True, executed_return=2.0),
        _row(rec_id="rec_below_cost", outcome_label="success", entry_triggered=True, executed_return=0.05),
        _row(rec_id="rec_loss", outcome_label="failed", entry_triggered=True, executed_return=-1.0),
    ]
    calls: list[dict] = []

    def scoped(_db, _start=None, _end=None, **kwargs):
        calls.append(kwargs)
        return rows

    monkeypatch.setattr("app.services.outcome_service._scoped_recommendation_rows", scoped)
    monkeypatch.setattr("app.services.benchmark_service.get_benchmark_close_map", lambda _db, _dates: {})
    monkeypatch.setattr("app.services.benchmark_service.compute_benchmark_return", lambda _close_map, _entry, _exit: 0.0)

    overview = _overview_metrics(MagicMock(), "2026-05-01", "2026-05-31", 30)
    d_net = _board_and_net_breakdown(MagicMock(), "2026-05-01", "2026-05-31", 30)

    assert overview["hit_rate"] == 0.333
    assert d_net["net"]["net_win_rate"] == 33.3
    assert overview["executed_trade_count"] == d_net["sample_count"] == 3
    assert all(
        call == {
            "run_mode": "formal",
            "live_only": True,
            "sample_scope": None,
            "historical_run_ids": None,
        }
        for call in calls
    )


def test_precision_at_k_uses_strict_accuracy_policy():
    rows = [
        _row(rec_id="rec_win", outcome_label="success", entry_triggered=True, executed_return=2.0, score=90),
        _row(rec_id="rec_label_success_loss", outcome_label="success", entry_triggered=True, executed_return=-0.2, score=85),
        _row(rec_id="rec_missed", outcome_label="missed", entry_triggered=False, executed_return=0.0, score=80),
    ]
    db = MagicMock()
    db.execute.return_value = _Rows(rows)

    result = _precision_at_k(db, (1, 2, 3))

    assert result[0]["precision"] == 1.0
    assert result[1]["precision"] == 0.5
    assert result[2]["precision"] == 0.333


def test_grouped_setup_metrics_include_recommendation_accuracy_denominator(monkeypatch):
    rows = [
        _row(rec_id="rec_win", outcome_label="success", entry_triggered=True, executed_return=2.0),
        _row(rec_id="rec_loss", outcome_label="failed", entry_triggered=True, executed_return=-1.0),
        _row(rec_id="rec_missed", outcome_label="missed", entry_triggered=False, executed_return=0.0),
    ]
    monkeypatch.setattr("app.services.benchmark_service.get_benchmark_close_map", lambda _db, _dates: {})
    monkeypatch.setattr("app.services.benchmark_service.compute_benchmark_return", lambda _close_map, _entry, _exit: 0.0)
    monkeypatch.setattr("app.services.outcome_service._scoped_recommendation_rows", lambda _db, _s=None, _e=None, **_k: rows)

    result = _metrics_by_setup(MagicMock())

    assert len(result) == 1
    row = result[0]
    assert row["labeled_count"] == 3
    assert row["triggered_count"] == 2
    assert row["missed_count"] == 1
    assert row["hit_rate"] == 0.5
    assert row["recommendation_accuracy"] == 0.333
    assert row["accuracy_denominator"] == 3
    assert row["average_net_return"] == round((2.0 - 1.0 + 0.0) / 3, 3)
    assert row["sample_confidence"] == "low"


def test_score_line_metrics_expose_9kpi_for_each_threshold(monkeypatch):
    rows = [
        _row(rec_id="rec_win", outcome_label="success", entry_triggered=True, executed_return=2.0, score=90),
        _row(rec_id="rec_loss", outcome_label="failed", entry_triggered=True, executed_return=-1.0, score=75),
        _row(rec_id="rec_missed", outcome_label="missed", entry_triggered=False, executed_return=0.0, score=65),
    ]
    monkeypatch.setattr("app.services.benchmark_service.get_benchmark_close_map", lambda _db, _dates: {})
    monkeypatch.setattr("app.services.benchmark_service.compute_benchmark_return", lambda _close_map, _entry, _exit: 0.0)
    monkeypatch.setattr("app.services.outcome_service._scoped_recommendation_rows", lambda _db, _s=None, _e=None, **_k: rows)

    result = _metrics_by_score_line(MagicMock(), (60, 80))

    low, high = result
    assert low["sample_count"] == 3
    assert low["recommendation_accuracy"] == 0.333
    assert low["accuracy_denominator"] == 3
    assert low["sample_confidence"] == "low"
    assert high["sample_count"] == 1
    assert high["recommendation_accuracy"] == 1.0
    assert high["accuracy_denominator"] == 1


def test_strategy_metrics_expose_9kpi_not_only_trade_hit_rate(monkeypatch):
    skill_json = '[{"strategy_id": "chen-xiaoqun-skill", "score": 80}]'
    rows = [
        _row(rec_id="rec_win", outcome_label="success", entry_triggered=True, executed_return=2.0, skill_breakdown_json=skill_json),
        _row(rec_id="rec_loss", outcome_label="failed", entry_triggered=True, executed_return=-1.0, skill_breakdown_json=skill_json),
        _row(rec_id="rec_missed", outcome_label="missed", entry_triggered=False, executed_return=0.0, skill_breakdown_json=skill_json),
    ]
    monkeypatch.setattr("app.services.benchmark_service.get_benchmark_close_map", lambda _db, _dates: {})
    monkeypatch.setattr("app.services.benchmark_service.compute_benchmark_return", lambda _close_map, _entry, _exit: 0.0)
    monkeypatch.setattr("app.services.outcome_service._scoped_recommendation_rows", lambda _db, _s=None, _e=None, **_k: rows)

    result = _metrics_by_strategy(MagicMock())

    assert len(result) == 1
    row = result[0]
    assert row["strategy_id"] == "chen-xiaoqun-skill"
    assert row["hit_rate"] == 0.5
    assert row["recommendation_accuracy"] == 0.333
    assert row["accuracy_denominator"] == 3
    assert row["sample_confidence"] == "low"
