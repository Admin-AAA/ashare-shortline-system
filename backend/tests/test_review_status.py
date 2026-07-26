from app.engine.review_status import classify_review_results


def test_review_status_distinguishes_verified_partial_degraded_and_rule_only():
    ok = {"review": {"runtime": "agent_skill_agno", "score": 80}, "error": None}
    failed = {"review": None, "error": "timeout"}

    assert classify_review_results([ok]) == "verified"
    assert classify_review_results([ok, failed]) == "partial"
    assert classify_review_results([failed]) == "degraded"
    assert classify_review_results([], agent_review_disabled=True) == "rule_only"
