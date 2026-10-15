from app.services.official_sample_scope import recommendation_history_conditions


def test_catchup_branch_only_when_ids_given():
    without = str(recommendation_history_conditions(live_run_ids=("a",))[0])
    with_ids = str(recommendation_history_conditions(live_run_ids=("a",), catchup_run_ids=("b",))[0])
    assert without.count("run_id IN") == 1
    assert with_ids.count("run_id IN") == 2
