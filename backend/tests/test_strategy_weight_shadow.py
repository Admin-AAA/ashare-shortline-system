from __future__ import annotations

import json

import pytest

from app.governance.store import GovernanceObjectStore
from app.services.analysis import shadow_weight
from app.services.analysis.shadow_weight import calculate_strategy_weight_shadow, create_strategy_weight_shadow


def _row(index: int, *, outcome: str = "success", strategy: str = "s", base: float = 1.0, month: str = "2026-01") -> dict:
    day = (index % 9) + 1
    return {
        "recommendation_id": f"rec_{strategy}_{index}",
        "strategy_id": strategy,
        "outcome_label": outcome,
        "trade_date": f"{month}-{day:02d}",
        "base_weight": base,
    }


def test_insufficient_evidence_keeps_effective_multiplier_at_one():
    rows = [_row(i, outcome="success", base=0.6) for i in range(29)]
    result = calculate_strategy_weight_shadow(rows)["strategies"][0]

    assert result["evaluated_count"] == 29
    assert result["status"] == "insufficient_evidence"
    assert result["effective_multiplier"] == 1.0
    assert result["suggested_weight"] == pytest.approx(0.6)
    assert result["posterior_mean"] == pytest.approx((29 + 15) / (29 + 15 + 15))


def test_bayesian_shadow_uses_formula_and_clamps_only_sufficient_samples():
    rows = [_row(i, outcome="success", base=1.1, month="2026-01" if i < 15 else "2026-02") for i in range(30)]
    result = calculate_strategy_weight_shadow(rows)["strategies"][0]

    # 30/30 has a posterior above the raw upper bound, so the active suggestion
    # is capped at 1.20 while the raw Bayesian multiplier remains auditable.
    assert result["posterior_mean"] == pytest.approx((30 + 15) / (30 + 15 + 15))
    assert result["raw_multiplier"] == pytest.approx(result["posterior_mean"] / 0.5)
    assert result["effective_multiplier"] == pytest.approx(1.20)
    assert result["suggested_weight"] == pytest.approx(1.1 * 1.20)
    assert result["distinct_days"] == 18
    assert result["distinct_months"] == 2
    assert result["credible_interval"]["lower"] < result["posterior_mean"] < result["credible_interval"]["upper"]


def test_exposure_diagnostics_include_unresolved_recommendations():
    rows = [_row(i, outcome="success") for i in range(30)]
    rows.append(_row(30, outcome="pending"))
    payload = calculate_strategy_weight_shadow(
        rows,
        eligible_recommendation_count=40,
        exposure_by_strategy={"s": 31},
    )
    result = payload["strategies"][0]

    assert result["evaluated_count"] == 30
    assert result["exposure_diagnostics"]["exposure_count"] == 31
    assert result["exposure_diagnostics"]["eligible_recommendation_count"] == 40
    assert result["exposure_diagnostics"]["pending_or_unresolved_count"] == 1
    assert result["exposure_diagnostics"]["exposure_rate"] == pytest.approx(31 / 40)


def test_shadow_persists_as_immutable_idempotent_governance_object(monkeypatch, tmp_path):
    captured = (
        [_row(i, outcome="success" if i < 16 else "failed") for i in range(30)],
        {"s": {f"rec_s_{i}" for i in range(30)}},
        {"s": [1.0]},
        30,
        0,
    )
    monkeypatch.setattr(shadow_weight, "_load_shadow_inputs", lambda db: captured)
    store = GovernanceObjectStore(tmp_path)

    first = create_strategy_weight_shadow(
        object(), store=store, created_by="test", code_revision="a" * 40
    )
    second = create_strategy_weight_shadow(
        object(), store=store, created_by="test", code_revision="a" * 40
    )

    assert first.object_id == second.object_id
    assert list(store.iter_kind("strategy_weight_shadow"))
    document = json.loads((tmp_path / "strategy_weight_shadow" / f"{first.object_id.rsplit(':', 1)[-1]}.json").read_text())
    assert document["payload"]["policy"]["activation"] == "shadow_only"
    assert document["payload"]["diagnostics"]["active_configuration_modified"] is False


def test_missing_code_revision_fails_closed(monkeypatch):
    monkeypatch.setattr(shadow_weight, "resolve_code_revision", lambda: "unknown")
    with pytest.raises(shadow_weight.ShadowWeightError, match="code revision"):
        create_strategy_weight_shadow(object(), store=GovernanceObjectStore("/tmp/p1-shadow-test"))
