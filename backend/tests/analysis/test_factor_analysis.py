from __future__ import annotations

from dataclasses import replace

import pytest

from app.domain.factor_analysis import (
    CrossSectionalObservation,
    FactorAnalysisError,
    FactorAnalysisRequest,
    evaluate_factor_cross_section,
)


def _observation(
    trade_date: str,
    code: str,
    factor: float | None,
    future_return: float | None,
    *,
    outcome_identity: str | None = None,
) -> CrossSectionalObservation:
    return CrossSectionalObservation(
        trade_date=trade_date,
        security_code=code,
        factor_name="avg_cost",
        factor_value=factor,
        future_return_pct=future_return,
        universe_scope="selected",
        evaluation_scope="live_forward",
        recommendation_id=f"rec_{trade_date}_{code}",
        outcome_identity=outcome_identity or f"outv_{trade_date}_{code}",
        source_artifact_ids=(f"source_{code}",),
    )


def _request(**overrides) -> FactorAnalysisRequest:
    values = {
        "factor_name": "avg_cost",
        "start_date": "2026-04-01",
        "end_date": "2026-05-31",
        "horizon_days": 5,
        "quantiles": 2,
        "min_cross_section_size": 4,
    }
    values.update(overrides)
    return FactorAnalysisRequest(**values)


def test_ic_quantiles_long_short_and_autocorrelation_are_hand_calculable():
    observations = []
    for date, returns in (
        ("2026-04-01", (1.0, 2.0, 3.0, 4.0)),
        ("2026-04-02", (2.0, 4.0, 6.0, 8.0)),
    ):
        observations.extend(
            _observation(date, f"00000{index}", float(index), value)
            for index, value in enumerate(returns, start=1)
        )

    result = evaluate_factor_cross_section(_request(end_date="2026-04-30"), observations)

    assert result.overall.pearson_ic.mean == pytest.approx(1.0)
    assert result.overall.rank_ic.mean == pytest.approx(1.0)
    assert result.overall.pearson_ic.std == pytest.approx(0.0)
    assert result.overall.pearson_ic.icir is None
    assert result.overall.pearson_ic.positive_ratio == pytest.approx(1.0)
    assert [item.average_return_pct for item in result.overall.quantiles] == pytest.approx(
        [2.25, 5.25]
    )
    assert result.overall.long_short_return_pct == pytest.approx(3.0)
    assert result.overall.monotonicity_spearman == pytest.approx(1.0)
    assert result.overall.factor_autocorrelation.mean == pytest.approx(1.0)
    assert result.overall.coverage == pytest.approx(1.0)


def test_average_rank_spearman_handles_ties_without_security_order_bias():
    factors = (1.0, 1.0, 2.0, 3.0)
    observations = tuple(
        _observation("2026-04-01", f"00000{index}", factor, float(index))
        for index, factor in enumerate(factors, start=1)
    )

    result = evaluate_factor_cross_section(_request(end_date="2026-04-30"), observations)

    assert result.overall.rank_ic.mean == pytest.approx(0.9486832980505138)
    assert [item.observation_count for item in result.overall.quantiles] == [2, 2]


def test_missing_zero_variance_and_small_sections_are_explicit_diagnostics():
    observations = (
        _observation("2026-04-01", "000001", 1.0, 1.0),
        _observation("2026-04-01", "000002", None, 2.0),
        _observation("2026-04-01", "000003", 2.0, None),
        _observation("2026-04-02", "000001", 1.0, 1.0),
        _observation("2026-04-02", "000002", 1.0, 2.0),
        _observation("2026-04-02", "000003", 1.0, 3.0),
        _observation("2026-04-02", "000004", 1.0, 4.0),
    )

    result = evaluate_factor_cross_section(_request(end_date="2026-04-30"), observations)
    by_date = {item.trade_date: item for item in result.overall.daily}

    assert by_date["2026-04-01"].coverage == pytest.approx(1 / 3)
    assert by_date["2026-04-01"].skip_reasons == ("insufficient_cross_section",)
    assert by_date["2026-04-02"].skip_reasons == ("zero_variance_factor",)
    assert result.overall.pearson_ic.count == 0
    assert result.diagnostics.evidence_status == "insufficient"


def test_months_are_fully_recomputed_then_aggregated_with_equal_weight():
    observations = []
    for date, returns in (
        ("2026-04-01", (1.0, 2.0, 3.0, 4.0)),
        ("2026-05-01", (1.0, 2.0, 7.0, 8.0)),
    ):
        observations.extend(
            _observation(date, f"00000{index}", float(index), value)
            for index, value in enumerate(returns, start=1)
        )

    result = evaluate_factor_cross_section(_request(), observations)

    assert [item.period for item in result.monthly] == ["2026-04", "2026-05"]
    assert [item.long_short_return_pct for item in result.monthly] == pytest.approx([2.0, 6.0])
    assert result.equal_weighted_monthly.long_short_return_pct == pytest.approx(4.0)
    assert result.diagnostics.max_month_observation_share == pytest.approx(0.5)


def test_input_order_and_source_id_order_do_not_change_identity_or_result():
    observations = tuple(
        replace(
            _observation("2026-04-01", f"00000{index}", float(index), float(index)),
            source_artifact_ids=("source_b", "source_a"),
        )
        for index in range(1, 5)
    )

    first = evaluate_factor_cross_section(_request(end_date="2026-04-30"), observations)
    second = evaluate_factor_cross_section(
        _request(end_date="2026-04-30"), tuple(reversed(observations))
    )

    assert first.observation_fingerprint == second.observation_fingerprint
    assert first.result_hash == second.result_hash
    assert first.to_canonical_json() == second.to_canonical_json()


def test_duplicate_identity_with_conflicting_value_fails_closed():
    first = _observation("2026-04-01", "000001", 1.0, 2.0)
    conflict = replace(first, factor_value=9.0)

    with pytest.raises(FactorAnalysisError, match="conflicting observation"):
        evaluate_factor_cross_section(_request(end_date="2026-04-30"), (first, conflict))


def test_request_rejects_noncanonical_return_field_and_mixed_scope():
    with pytest.raises(FactorAnalysisError, match="net_return_pct"):
        _request(return_field="executed_return")

    observations = [
        _observation("2026-04-01", f"00000{index}", float(index), float(index))
        for index in range(1, 5)
    ]
    observations[0] = replace(observations[0], evaluation_scope="historical_replay")
    with pytest.raises(FactorAnalysisError, match="scope"):
        evaluate_factor_cross_section(_request(end_date="2026-04-30"), observations)
