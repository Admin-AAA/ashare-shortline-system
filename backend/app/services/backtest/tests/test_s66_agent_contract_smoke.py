"""S6.6 Agent contract smoke: prove self-improve can complete the research loop.

This test runs the full Agent Research Gateway loop against a real strategy
on real MySQL when ``RUN_REAL_MYSQL_S6=1`` is set, and verifies that the
research boundary cannot mutate formal strategy/recommendation/outcome data.

When the environment variable is not set, the test is skipped (it requires
local MySQL with real strategy data).
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

from app.db.session import get_db
from app.engine.backtest import BacktestRequest, ExecutionPolicy, SingleDate
from app.services.backtest.research_repository import ResearchBacktestRepository
from app.services.backtest.research_runner import ResearchBacktestRunner

RUN_REAL_MYSQL = os.environ.get("RUN_REAL_MYSQL_S6") == "1"


def _recommendation_counts(db) -> tuple[int, int]:
    """Return (recommendation_count, outcome_count) as a baseline sanity check."""
    from sqlalchemy import text

    rec_count = db.execute(text("SELECT COUNT(*) FROM recommendations")).scalar() or 0
    outcome_count = db.execute(text("SELECT COUNT(*) FROM recommendation_outcomes")).scalar() or 0
    return int(rec_count), int(outcome_count)


@pytest.mark.skipif(not RUN_REAL_MYSQL, reason="requires local MySQL with real strategy data")
def test_agent_contract_smoke_research_loop():
    """End-to-end Agent contract smoke: preflight → run → candidates → trades → attributions.

    Verifies that:
    1. The research loop completes on a real declarative strategy.
    2. Gateway writes only to dedicated backtest_* tables, never to
       recommendations/recommendation_outcomes.
    3. Attribution aggregates are machine-readable.
    """
    db = next(get_db())
    try:
        rec_before, outcome_before = _recommendation_counts(db)

        request = BacktestRequest(
            strategy_id="daily-stock-analysis-skill",
            date_selector=SingleDate("2026-06-05"),
            execution_policy=ExecutionPolicy(),
        )
        repo = ResearchBacktestRepository(db)
        runner = ResearchBacktestRunner(repo)

        # Step 1: preflight
        preflight = runner.preflight(request)
        assert preflight["strategy_id"] == "daily-stock-analysis-skill"
        assert "snapshot_id" in preflight

        # Step 2: run
        result = runner.run(request)
        backtest_id = result["backtest_id"]
        assert result["status"] in {"completed", "insufficient_data", "incomplete_evaluation"}

        # Step 3: detail
        detail = repo.get_run(backtest_id)
        assert detail.strategy_id == "daily-stock-analysis-skill"
        assert detail.snapshot_id == result["snapshot_id"]

        # Step 4: candidates
        candidates = repo.list_candidates(backtest_id)
        assert isinstance(candidates, list)

        # Step 5: trades
        trades = repo.list_trades(backtest_id)
        assert isinstance(trades, list)

        # Step 6: attributions + aggregates
        attributions = repo.list_attributions(backtest_id)
        aggregates = repo.aggregate_attributions(backtest_id)
        assert isinstance(aggregates, list)
        for agg in aggregates:
            assert "reason_code" in agg
            assert "count" in agg
            assert "ratio" in agg

        # Step 7: replay (force_rerun)
        if result["status"] == "completed":
            replay = runner.replay(backtest_id, force_rerun=True)
            assert replay["attempt_no"] == 2
            assert replay["backtest_id"] != backtest_id
            # Replay must produce identical metrics (snapshot integrity)
            original_metrics = result["result"].get("metrics", {})
            replay_metrics = replay["result"].get("metrics", {})
            assert original_metrics == replay_metrics

        # Critical: Gateway must NOT write to recommendations/recommendation_outcomes
        rec_after, outcome_after = _recommendation_counts(db)
        assert rec_after == rec_before, "Gateway must not write to recommendations table"
        assert outcome_after == outcome_before, "Gateway must not write to recommendation_outcomes table"

        db.commit()
    finally:
        db.close()


def test_agent_contract_research_only_boundary():
    """Verify the research repository never imports Recommendation/RecommendationOutcome ORM."""
    import inspect
    from app.services.backtest import research_repository, research_runner

    repo_source = inspect.getsource(research_repository)
    runner_source = inspect.getsource(research_runner)

    # The research boundary must not import the Recommendation ORM model.
    import_lines = [line for line in repo_source.split("\n") if line.strip().startswith(("from ", "import "))]
    for line in import_lines:
        assert "Recommendation" not in line or "Backtest" in line, (
            f"research_repository.py must not import Recommendation ORM: {line}"
        )

    assert "run_backtest(" not in runner_source, (
        "research_runner.py must not call the legacy run_backtest service"
    )
    # Confirm the dedicated backtest ORM models are the only write targets.
    for model_name in ("BacktestDataSnapshot", "BacktestInputArtifact", "BacktestRequestClaim",
                       "BacktestRunV2", "BacktestCandidate", "BacktestTrade",
                       "BacktestFailureAttribution", "BacktestExperiment"):
        assert model_name in repo_source, (
            f"expected {model_name} to be referenced by the research boundary"
        )
