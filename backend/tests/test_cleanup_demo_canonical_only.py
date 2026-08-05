from scripts.cleanup_demo_canonical_only import (
    FULL_CLEAR_TABLES,
    INITIAL_CLEANUP_OBJECT_ID,
    MUTATION_ALLOWLIST,
    SCOPED_DELETE_POLICIES,
)


def test_demo_cleanup_mutation_targets_are_explicit_and_scoped() -> None:
    expected_full_clear = {
        "recommendation_evidence_classification_audits",
        "recommendation_execution_states",
        "recommendation_outcome_versions",
        "recommendation_outcomes",
        "recommendations",
        "recommendation_runs",
        "llm_review_cache",
    }
    assert set(FULL_CLEAR_TABLES) == expected_full_clear
    assert SCOPED_DELETE_POLICIES == {
        "api_call_logs": "run_id IN deleted recommendation_runs",
    }
    assert set(MUTATION_ALLOWLIST) == expected_full_clear | {"api_call_logs"}
    assert MUTATION_ALLOWLIST["api_call_logs"] != "all rows"
    assert INITIAL_CLEANUP_OBJECT_ID.endswith(
        "113d7c42ccac03bd25d7571bafe57b6955fc3fd725f0312821fa3ae270bc81ab"
    )
