from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.outcome_service import (
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
) -> SimpleNamespace:
    return SimpleNamespace(
        recommendation_id=rec_id,
        run_id="run_accuracy_policy",
        code=rec_id[-6:].rjust(6, "0"),
        name=rec_id,
        final_score=score,
        setup_type="突破",
        skill_breakdown_json="[]",
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
    monkeypatch.setattr("app.services.outcome_service._scoped_recommendation_rows", lambda _db, _mode: rows)
    monkeypatch.setattr("app.services.benchmark_service.get_benchmark_close_map", lambda _db, _dates: {})
    monkeypatch.setattr("app.services.benchmark_service.compute_benchmark_return", lambda _close_map, _entry, _exit: 0.0)

    metrics = _overview_metrics(MagicMock(), "formal")

    assert metrics["labeled_count"] == 3
    assert metrics["triggered_count"] == 3
    assert metrics["success_count"] == 1
    assert metrics["failed_count"] == 2
    assert metrics["recommendation_accuracy"] == 0.333
    assert metrics["trade_accuracy"] == 0.333
    assert metrics["accuracy_basis"] == "labeled_recommendations"


def test_precision_at_k_uses_strict_accuracy_policy():
    rows = [
        _row(rec_id="rec_win", outcome_label="success", entry_triggered=True, executed_return=2.0, score=90),
        _row(rec_id="rec_label_success_loss", outcome_label="success", entry_triggered=True, executed_return=-0.2, score=85),
        _row(rec_id="rec_missed", outcome_label="missed", entry_triggered=False, executed_return=0.0, score=80),
    ]
    db = MagicMock()
    db.execute.return_value = _Rows(rows)

    result = _precision_at_k(db, "formal", (1, 2, 3))

    assert result[0]["precision"] == 1.0
    assert result[1]["precision"] == 0.5
    assert result[2]["precision"] == 0.333
