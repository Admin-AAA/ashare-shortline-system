import app.services.single_stock_analysis as m


def test_single_stock_request_disables_repeat_filters(db):
    request, _ = m._single_stock_run_request(
        db, trade_date="2026-10-15", include_llm=False, skill_id="chen-xiaoqun-skill", preset_id=None
    )
    assert request.hide_recent_repeats is False
    assert request.dedup_same_day is False


def test_prior_formal_recommendations_shape(db):
    out = m._prior_formal_recommendations(db, "000000", "2026-10-15")
    assert out["available"] is True and out["recent_7d"] is False and out["items"] == []
