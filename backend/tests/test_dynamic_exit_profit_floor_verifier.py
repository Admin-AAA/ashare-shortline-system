from scripts.verify_dynamic_exit_profit_floor import (
    passes_production_gate,
    select_development_candidate,
    split_development_validation,
)


def _sample(trade_date: str, recommendation_id: str) -> dict:
    return {"trade_date": trade_date, "recommendation_id": recommendation_id}


def test_split_development_validation_uses_first_two_thirds_of_unique_dates():
    samples = [
        _sample("2026-07-01", "r2"),
        _sample("2026-07-01", "r1"),
        _sample("2026-07-02", "r3"),
        _sample("2026-07-03", "r4"),
        _sample("2026-07-04", "r5"),
        _sample("2026-07-05", "r6"),
        _sample("2026-07-06", "r7"),
    ]

    development, validation = split_development_validation(samples)

    assert [samples[i]["trade_date"] for i in development] == [
        "2026-07-01",
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
        "2026-07-04",
    ]
    assert [samples[i]["recommendation_id"] for i in development[:2]] == ["r1", "r2"]
    assert {samples[i]["trade_date"] for i in development}.isdisjoint(
        {samples[i]["trade_date"] for i in validation}
    )


def test_development_selection_only_considers_candidates_passing_every_gate():
    fixed = {
        "avg_net": 2.0,
        "win_rate": 60.0,
        "max_loss": -10.0,
        "sharpe": 0.4,
        "profit_factor": 1.4,
        "expected_downside": 2.5,
    }
    candidates = {
        "floor_25": {
            "avg_net": 2.2,
            "win_rate": 65.0,
            "max_loss": -10.0,
            "sharpe": 0.5,
            "profit_factor": 1.5,
            "expected_downside": 2.3,
        },
        "floor_50": {
            "avg_net": 2.6,
            "win_rate": 70.0,
            "max_loss": -10.0,
            "sharpe": 0.6,
            "profit_factor": 1.7,
            "expected_downside": 2.0,
        },
        "loss_worse": {
            "avg_net": 2.8,
            "win_rate": 70.0,
            "max_loss": -10.0,
            "sharpe": 0.6,
            "profit_factor": 1.6,
            "expected_downside": 2.6,
        },
    }

    assert passes_production_gate(fixed, candidates["floor_25"])
    assert passes_production_gate(fixed, candidates["floor_50"])
    assert not passes_production_gate(fixed, candidates["loss_worse"])
    assert select_development_candidate(fixed, candidates) == "floor_50"
