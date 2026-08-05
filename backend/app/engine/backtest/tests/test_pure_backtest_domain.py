"""Independent unit tests for the ORM-free P0 backtest domain API."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.engine.backtest import (
    ArtifactRef,
    ArtifactScope,
    BacktestErrorCode,
    BacktestRequest,
    BacktestValidationError,
    BarInput,
    CoverageResult,
    CoverageStatus,
    DataSnapshot,
    DateRange,
    ExecutionPolicy,
    FailureReasonCode,
    FutureDataAccessDeniedError,
    PartitionManifest,
    RecommendationInput,
    SignalDateDataReader,
    SignalInput,
    SignalInputRequirement,
    SingleDate,
    SnapshotIntegrityError,
    TradeOutcome,
)
from app.engine.simulation import attribute_failures, build_equity_curve, compute_metrics, simulate


def _policy(**overrides: object) -> ExecutionPolicy:
    values: dict[str, object] = {
        "entry_lookahead_days": 2,
        "horizon_days": 3,
        "commission_rate": 0.0,
        "stamp_tax_rate": 0.0,
        "entry_slippage_bps": 0.0,
        "exit_slippage_bps": 0.0,
        "stop_loss_pct": 5.0,
        "take_profit_pct": 10.0,
    }
    values.update(overrides)
    return ExecutionPolicy(**values)


def _bars() -> tuple[BarInput, ...]:
    return (
        BarInput("000001", "2024-01-02", 100, 101, 94, 96),
        BarInput("000001", "2024-01-03", 96, 97, 95, 96),
        BarInput("000001", "2024-01-04", 96, 97, 95, 96),
    )


def test_request_is_immutable_single_strategy_and_ignores_force_rerun_for_fingerprint() -> None:
    request = BacktestRequest(
        strategy_id="daily-stock",
        strategy_version="v1",
        date_selector=SingleDate("2024-01-01"),
        execution_policy=_policy(),
        target_codes=("000002", "000001"),
    )
    forced = BacktestRequest(
        strategy_id="daily-stock",
        strategy_version="v1",
        date_selector=SingleDate("2024-01-01"),
        execution_policy=_policy(),
        target_codes=("000001", "000002"),
        force_rerun=True,
    )

    assert request.diagnostic_scope is True
    assert request.target_codes == ("000001", "000002")
    assert request.fingerprint() == forced.fingerprint()
    assert request.request_fingerprint(snapshot_id="snapshot-1") == forced.request_fingerprint(snapshot_id="snapshot-1")
    with pytest.raises(FrozenInstanceError):
        request.strategy_id = "another"  # type: ignore[misc]
    with pytest.raises(BacktestValidationError) as exc_info:
        BacktestRequest(
            strategy_id="",
            date_selector=SingleDate("2024-01-01"),
            execution_policy=_policy(),
        )
    assert exc_info.value.code == BacktestErrorCode.INVALID_REQUEST.value


def test_date_range_is_inclusive_contract_and_rejects_reverse_range() -> None:
    selector = DateRange("2024-01-01", "2024-01-31")
    assert selector.start_date == "2024-01-01"
    assert selector.end_date == "2024-01-31"
    with pytest.raises(BacktestValidationError) as exc_info:
        DateRange("2024-01-31", "2024-01-01")
    assert exc_info.value.code == BacktestErrorCode.INVALID_DATE_SELECTOR.value


def test_signal_input_rejects_future_visibility_and_reader_protocol_is_structural() -> None:
    with pytest.raises(FutureDataAccessDeniedError) as exc_info:
        SignalInput(
            signal_date="2024-01-02",
            visible_at="2024-01-03",
            payload={"close": 10},
        )
    assert exc_info.value.code == BacktestErrorCode.FUTURE_DATA_ACCESS_DENIED.value

    class Reader:
        def read(self, signal_date: str, requirement: SignalInputRequirement) -> SignalInput:
            return SignalInput(signal_date=signal_date, visible_at=signal_date, payload={"lookback": requirement.lookback_bars})

    assert isinstance(Reader(), SignalDateDataReader)


def test_snapshot_manifest_and_artifact_content_are_hash_verified_and_immutable() -> None:
    payload = b'{"bars":[1,2,3]}'
    artifact = ArtifactRef.from_content(
        artifact_id="artifact-1",
        signal_date="2024-01-02",
        artifact_scope=ArtifactScope.SIGNAL_INPUT,
        artifact_type="daily-bars",
        schema_version=1,
        row_count=3,
        payload=payload,
    )
    partition = PartitionManifest(
        dataset="daily_kbars",
        query_range=DateRange("2023-12-20", "2024-01-02"),
        source_version="daily_kbars:v1",
        row_count=10,
        content_hash="a" * 64,
    )
    snapshot = DataSnapshot(
        snapshot_id="snapshot-1",
        snapshot_version=1,
        partitions=(partition,),
        artifacts_by_signal_date={"2024-01-02": (artifact,)},
        coverage_by_signal_date={
            "2024-01-02": CoverageResult("2024-01-02", CoverageStatus.READY, 1.0),
        },
    )

    assert snapshot.input_manifest_hash == snapshot.calculated_manifest_hash()
    assert snapshot.artifacts_for("2024-01-02") == (artifact,)
    with pytest.raises(TypeError):
        snapshot.artifacts_by_signal_date["2024-01-03"] = ()  # type: ignore[index]
    artifact.validate_content(payload)
    with pytest.raises(SnapshotIntegrityError) as exc_info:
        artifact.validate_content(b"changed")
    assert exc_info.value.code == BacktestErrorCode.SNAPSHOT_INTEGRITY_ERROR.value


def test_simulation_is_deterministic_and_conservative_stop_loss_drives_failure_attribution() -> None:
    recommendation = RecommendationInput(code="000001", signal_date="2024-01-01", signal_price=100)
    result_one = simulate(recommendation, _bars(), _policy())
    result_two = simulate(recommendation, tuple(reversed(_bars())), _policy())

    assert result_one == result_two
    assert result_one.entry_triggered is True
    assert result_one.trade.outcome_label is TradeOutcome.FAILED
    assert result_one.trade.exit_reason == "stop_loss_hit"
    assert result_one.trade.net_return_pct == -5.0
    attributions = attribute_failures((result_one.trade,))
    assert attributions[0].reason_code is FailureReasonCode.STOP_LOSS
    assert attributions[0].unknown is False


def test_gap_rejection_is_not_a_trade_and_never_enters_failure_attribution() -> None:
    result = simulate(
        RecommendationInput(code="000001", signal_date="2024-01-01", signal_price=100),
        _bars(),
        _policy(max_entry_gap_pct=0.0),
    )
    # First eligible bar opens at signal price, so use a strict negative gap rule
    # only to exercise the normal no-entry path through the available contract.
    assert result.entry_triggered is True

    rejected = simulate(
        RecommendationInput(code="000001", signal_date="2024-01-01", signal_price=90),
        _bars(),
        _policy(max_entry_gap_pct=5.0),
    )
    assert rejected.entry_triggered is False
    assert rejected.trade.outcome_label is TradeOutcome.NOT_EXECUTED
    assert attribute_failures((rejected.trade,)) == ()


def test_default_max_entry_gap_pct_aligns_with_production_engine() -> None:
    # 引擎默认 max_entry_gap_pct 现已对齐生产 MAX_ENTRY_GAP_PCT=10：
    # 否则回测会纳入生产已整笔丢弃的跳空高开票（历史 12/12 全亏），
    # 破坏 T1/T2 回测（D 项核心工具）与生产胜率的可比性。
    assert ExecutionPolicy().max_entry_gap_pct == 10.0

    # 注册 policy 的 entry window 为 2 日：首日跳空超过阈值会跳过，
    # 第二日若回到阈值内仍可入场；这与 artifact 的显式窗口一致。
    gapped = simulate(
        RecommendationInput(code="000001", signal_date="2024-01-01", signal_price=90),
        _bars(),
        ExecutionPolicy(),
    )
    assert gapped.entry_triggered is True
    assert gapped.trade.outcome_label is not TradeOutcome.NOT_EXECUTED

    # 无跳空（信号价=开盘价）→ 正常入场，证明默认仅拦跳空、不误杀正常候选。
    normal = simulate(
        RecommendationInput(code="000001", signal_date="2024-01-01", signal_price=100),
        _bars(),
        ExecutionPolicy(),
    )
    assert normal.entry_triggered is True


def test_metrics_and_equity_curve_are_deterministic_and_exclude_unexecuted_samples() -> None:
    loss = simulate(RecommendationInput("000001", "2024-01-01", 100), _bars(), _policy()).trade
    win_bars = (
        BarInput("000002", "2024-01-02", 100, 106, 99, 105),
        BarInput("000002", "2024-01-03", 105, 106, 104, 105),
    )
    win = simulate(
        RecommendationInput("000002", "2024-01-01", 100),
        win_bars,
        _policy(stop_loss_pct=None, take_profit_pct=None, horizon_days=2),
    ).trade
    not_executed = simulate(
        RecommendationInput("000003", "2024-01-01", 100),
        (),
        _policy(),
    ).trade

    metrics = compute_metrics((loss, win, not_executed))
    curve = build_equity_curve((loss, win, not_executed))
    assert metrics.sample_count == 3
    assert metrics.executed_trade_count == 2
    assert metrics.unexecuted_count == 1
    assert metrics.win_count == 1
    assert metrics.loss_count == 1
    # T+1 后两笔都在 2024-01-03 退出，日聚合净收益相抵；不再用
    # 入场日伪退出人为制造一条单独的回撤点。
    assert metrics.max_drawdown_pct == 0
    assert len(curve) == 1


def test_engine_domain_has_no_orm_or_service_imports() -> None:
    engine = Path(__file__).resolve().parents[2]
    checked = (engine / "simulation.py",) + tuple((engine / "backtest").glob("*.py"))
    forbidden = ("sqlalchemy", "app.services", "Session")
    for source in checked:
        text = source.read_text(encoding="utf-8")
        assert not any(token in text for token in forbidden), source
