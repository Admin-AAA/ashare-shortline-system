"""Research-only failure attribution contract tests."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.tables import (
    BacktestDataSnapshot,
    BacktestFailureAttribution,
    BacktestRequestClaim,
    BacktestRunV2,
    BacktestTrade,
)
from app.services.backtest.research_repository import (
    AttributionValidationError,
    ResearchBacktestRepository,
)


@pytest.fixture
def repository_and_run():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            BacktestDataSnapshot.__table__,
            BacktestRequestClaim.__table__,
            BacktestRunV2.__table__,
            BacktestTrade.__table__,
            BacktestFailureAttribution.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    try:
        repository = ResearchBacktestRepository(session)
        repository.persist_snapshot(
            snapshot_id="snapshot_attribution_fixture",
            snapshot_version=2,
            partition_manifest={"schema_version": 2, "partitions": [], "artifacts_by_signal_date": {}},
            coverage={},
            missing_requirements=[],
        )
        claim = repository.claim_request(
            request_fingerprint="a" * 64,
            backtest_id="btr_attribution_fixture",
            snapshot_id="snapshot_attribution_fixture",
            strategy_id="attribution-strategy",
            strategy_version="strategy-v1",
            request_schema_version=1,
            request_json={"strategy_id": "attribution-strategy"},
            execution_version="execution-v1:attribution",
            execution_policy_fingerprint="b" * 64,
            signal_date="2026-01-02",
        )
        repository.persist_trades(
            claim.backtest_id,
            [
                {
                    "trade_id": "btt_failed",
                    "signal_date": "2026-01-02",
                    "code": "000001",
                    "entry_date": "2026-01-03",
                    "exit_date": "2026-01-04",
                    "outcome_label": "failed",
                    "trade_json": {"net_return_pct": -3.0},
                },
                {
                    "trade_id": "btt_no_signal",
                    "signal_date": "2026-01-02",
                    "code": "000002",
                    "entry_date": None,
                    "exit_date": None,
                    "outcome_label": "no_signal",
                    "trade_json": {},
                },
            ],
        )
        session.commit()
        yield repository, claim.backtest_id
    finally:
        session.close()
        engine.dispose()


def test_all_stable_failure_reason_codes_are_persisted_and_aggregated(repository_and_run) -> None:
    repository, backtest_id = repository_and_run
    reason_codes = (
        "chase_high",
        "stop_loss",
        "sector_decay",
        "black_swan",
        "execution_failure",
        "unknown",
    )
    repository.persist_attributions(
        backtest_id,
        [
            {
                "trade_id": "btt_failed",
                "reason_code": reason_code,
                "rule_or_factor_reference": "fixture-rule",
                "evidence_references": [{"reference": "artifact_fixture", "kind": "artifact"}],
                "confidence": 0.0 if reason_code == "unknown" else 0.8,
                "is_unknown": reason_code == "unknown",
            }
            for reason_code in reason_codes
        ],
    )

    assert {item.reason_code for item in repository.list_attributions(backtest_id)} == set(reason_codes)
    aggregates = repository.aggregate_attributions(backtest_id)
    assert {item["reason_code"] for item in aggregates} == set(reason_codes)
    assert all(item["ratio"] == 1.0 for item in aggregates)


def test_no_signal_trade_cannot_receive_failure_attribution(repository_and_run) -> None:
    repository, backtest_id = repository_and_run

    with pytest.raises(AttributionValidationError, match="已成交 trade"):
        repository.persist_attributions(
            backtest_id,
            [
                {
                    "trade_id": "btt_no_signal",
                    "reason_code": "unknown",
                    "evidence_references": [],
                    "confidence": 0.0,
                    "is_unknown": True,
                }
            ],
        )
