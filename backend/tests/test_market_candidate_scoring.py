from app.services.market_data import _candidate_score


def _candidate(**overrides):
    item = {
        "amount_yi": 5.0,
        "pct": 3.0,
        "chg60": 15.0,
        "turnover_rate": 5.0,
    }
    item.update(overrides)
    return item


def test_candidate_score_prefers_tradeable_mid_strength_over_overheated_popularity():
    tradeable = _candidate(amount_yi=6.0, pct=3.5, chg60=18.0, turnover_rate=4.5)
    overheated = _candidate(amount_yi=18.0, pct=9.5, chg60=68.0, turnover_rate=13.0)

    assert _candidate_score(tradeable) > _candidate_score(overheated)


def test_candidate_score_penalizes_too_low_turnover():
    healthy = _candidate(turnover_rate=4.0)
    sleepy = _candidate(turnover_rate=0.4)

    assert _candidate_score(healthy) > _candidate_score(sleepy)


def test_candidate_score_penalizes_blowoff_pct():
    controlled = _candidate(pct=3.0)
    blowoff = _candidate(pct=10.0)

    assert _candidate_score(controlled) > _candidate_score(blowoff)
