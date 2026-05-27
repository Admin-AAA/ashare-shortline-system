from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.calibrator import _load_labeled_samples


class _Rows:
    def __init__(self, rows: list[tuple[SimpleNamespace, SimpleNamespace]]):
        self._rows = rows

    def all(self) -> list[tuple[SimpleNamespace, SimpleNamespace]]:
        return self._rows


def _rec(rec_id: str, score: float) -> SimpleNamespace:
    return SimpleNamespace(
        recommendation_id=rec_id,
        code=rec_id[-6:].rjust(6, "0"),
        trade_date="2026-05-12",
        feature_snapshot_json=json.dumps(
            {
                "component_scores": {
                    "skill_agent": score,
                    "strategy_rule": score - 5,
                    "market": 70.0,
                    "risk": 80.0,
                    "evidence": 65.0,
                }
            }
        ),
    )


def _outcome(*, label: str, executed_return: float) -> SimpleNamespace:
    return SimpleNamespace(
        outcome_label=label,
        entry_triggered=True,
        executed_return=executed_return,
        close_return=executed_return,
    )


def test_calibration_samples_use_executed_positive_return_as_label():
    db = MagicMock()
    db.execute.return_value = _Rows(
        [
            (_rec("rec_win", 90.0), _outcome(label="success", executed_return=2.0)),
            (_rec("rec_label_success_loss", 85.0), _outcome(label="success", executed_return=-0.2)),
            (_rec("rec_failed_loss", 80.0), _outcome(label="failed", executed_return=-1.0)),
        ]
    )

    samples = _load_labeled_samples(db)

    assert [sample["label"] for sample in samples] == [1, 0, 0]
    assert [sample["close_return"] for sample in samples] == [2.0, -0.2, -1.0]
