"""Frozen historical-holdout validation for shadow strategy quality policies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.backtest import Trade, canonical_fingerprint
from app.engine.simulation import compute_metrics
from app.governance.holdout import HoldoutPopulationIdentity
from app.models.tables import BacktestTrade
from app.services.backtest.research_repository import ResearchBacktestRepository
from app.services.backtest.research_runner import ResearchBacktestRunner
from app.services.backtest.strategy_quality import (
    QUALITY_SPEC_VERSION,
    QualityCostPolicy,
    StrategyQualityConfig,
    StrategyTradeEvidence,
    apply_quality_gate,
    compute_quality_profile,
    reprice_quality_evidences,
)


FROZEN_QUALITY_VALIDATION_VERSION = "strategy-quality-frozen-holdout:v1"


@dataclass(frozen=True, slots=True)
class FrozenQualityAcceptance:
    min_executed_trades: int = 100
    min_signal_days: int = 20
    min_represented_regimes: int = 2
    min_average_net_return_pct: float = 0.0
    min_profit_factor: float = 1.10
    fold_count: int = 3
    min_positive_folds: int = 2

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "min_executed_trades": self.min_executed_trades,
            "min_signal_days": self.min_signal_days,
            "min_represented_regimes": self.min_represented_regimes,
            "min_average_net_return_pct": self.min_average_net_return_pct,
            "min_profit_factor": self.min_profit_factor,
            "fold_count": self.fold_count,
            "min_positive_folds": self.min_positive_folds,
            "max_drawdown_rule": "gated_no_worse_than_raw_holdout",
        }


@dataclass(frozen=True, slots=True)
class FrozenQualityValidationSpec:
    strategy_id: str
    strategy_version: str
    holdout_date_from: str
    holdout_date_to: str
    discovery_date_from: str
    expected_signal_days: int
    signal_dates_fingerprint: str
    quality_fingerprint: str
    execution_policy_fingerprint: str
    parameter_fingerprint: str
    acceptance: FrozenQualityAcceptance = FrozenQualityAcceptance()

    def __post_init__(self) -> None:
        start = date.fromisoformat(self.holdout_date_from)
        end = date.fromisoformat(self.holdout_date_to)
        discovery = date.fromisoformat(self.discovery_date_from)
        if start > end:
            raise ValueError("holdout_date_from must not follow holdout_date_to")
        if end >= discovery:
            raise ValueError("historical holdout must end before discovery starts")
        if (discovery - end).days < 14:
            raise ValueError("historical holdout requires a 14-calendar-day boundary gap")
        if self.expected_signal_days <= 0:
            raise ValueError("expected_signal_days must be positive")
        for name in (
            "signal_dates_fingerprint",
            "quality_fingerprint",
            "execution_policy_fingerprint",
            "parameter_fingerprint",
        ):
            value = getattr(self, name)
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if self.acceptance.fold_count < 3:
            raise ValueError("frozen holdout requires at least three chronological folds")
        if not 0 < self.acceptance.min_positive_folds <= self.acceptance.fold_count:
            raise ValueError("min_positive_folds must be within fold_count")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": FROZEN_QUALITY_VALIDATION_VERSION,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "holdout_date_from": self.holdout_date_from,
            "holdout_date_to": self.holdout_date_to,
            "discovery_date_from": self.discovery_date_from,
            "expected_signal_days": self.expected_signal_days,
            "signal_dates_fingerprint": self.signal_dates_fingerprint,
            "boundary_gap_rule": "at_least_14_calendar_days",
            "quality_fingerprint": self.quality_fingerprint,
            "execution_policy_fingerprint": self.execution_policy_fingerprint,
            "parameter_fingerprint": self.parameter_fingerprint,
            "acceptance": self.acceptance.canonical_payload(),
            "promotion_scope": "historical_qualification_only_forward_canary_required",
        }

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.canonical_payload())


@dataclass(frozen=True, slots=True)
class FrozenQualityProvenance:
    backtest_id: str
    snapshot_id: str
    input_manifest_hash: str
    snapshot_manifest_hash: str
    execution_version: str
    request_fingerprint: str
    strategy_version: str
    execution_policy_fingerprint: str
    parameter_fingerprint: str
    holdout_population_fingerprint: str
    claim_experiment_id: str
    output_evidence_fingerprint: str
    stock_metadata_temporality: str

    def __post_init__(self) -> None:
        for name in (
            "input_manifest_hash",
            "snapshot_manifest_hash",
            "request_fingerprint",
            "execution_policy_fingerprint",
            "parameter_fingerprint",
            "holdout_population_fingerprint",
            "output_evidence_fingerprint",
        ):
            value = str(getattr(self, name))
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        for name in (
            "backtest_id",
            "snapshot_id",
            "execution_version",
            "strategy_version",
            "claim_experiment_id",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must be non-empty")
        if self.snapshot_id != f"bts_{self.input_manifest_hash}":
            raise ValueError("snapshot_id does not match the verified input manifest")
        if self.stock_metadata_temporality not in {"point_in_time", "current_static", "unknown"}:
            raise ValueError("stock_metadata_temporality is invalid")

    def canonical_payload(self) -> dict[str, str]:
        return {
            "backtest_id": self.backtest_id,
            "snapshot_id": self.snapshot_id,
            "input_manifest_hash": self.input_manifest_hash,
            "snapshot_manifest_hash": self.snapshot_manifest_hash,
            "execution_version": self.execution_version,
            "request_fingerprint": self.request_fingerprint,
            "strategy_version": self.strategy_version,
            "execution_policy_fingerprint": self.execution_policy_fingerprint,
            "parameter_fingerprint": self.parameter_fingerprint,
            "holdout_population_fingerprint": self.holdout_population_fingerprint,
            "claim_experiment_id": self.claim_experiment_id,
            "output_evidence_fingerprint": self.output_evidence_fingerprint,
            "stock_metadata_temporality": self.stock_metadata_temporality,
        }


def quality_policy_fingerprint(
    config: StrategyQualityConfig,
    cost_policy: QualityCostPolicy,
) -> str:
    return canonical_fingerprint(
        {
            "spec_version": QUALITY_SPEC_VERSION,
            "config": config.canonical_payload(),
            "cost_policy": cost_policy.canonical_payload(),
        }
    )


def evaluate_frozen_quality_backtest(
    db: Session,
    *,
    backtest_id: str,
    spec: FrozenQualityValidationSpec,
    config: StrategyQualityConfig,
    cost_policy: QualityCostPolicy,
) -> dict[str, Any]:
    """Load and verify immutable evidence before evaluating one frozen candidate."""

    if config.strategy_id != spec.strategy_id:
        raise ValueError("quality config strategy_id differs from frozen spec")
    if quality_policy_fingerprint(config, cost_policy) != spec.quality_fingerprint:
        raise ValueError("quality policy differs from frozen spec")

    repository = ResearchBacktestRepository(db)
    run = repository.get_run(backtest_id)
    if str(run.status) != "completed":
        raise ValueError("frozen quality backtest must be completed")
    if str(run.strategy_id) != spec.strategy_id or str(run.strategy_version) != spec.strategy_version:
        raise ValueError("backtest strategy identity differs from frozen spec")
    if str(run.execution_policy_fingerprint) != spec.execution_policy_fingerprint:
        raise ValueError("backtest execution policy differs from frozen spec")

    snapshot_row = repository.get_snapshot(str(run.snapshot_id))
    request_payload = _json_object(str(run.request_json), "request_json")
    semantic_request = dict(request_payload)
    semantic_request.pop("force_rerun", None)
    expected_request_fingerprint = canonical_fingerprint(
        {
            "request": semantic_request,
            "snapshot_id": str(run.snapshot_id),
            "effective_strategy_version": str(run.strategy_version),
            "execution_version": str(run.execution_version),
            "execution_policy_fingerprint": str(run.execution_policy_fingerprint),
        }
    )
    if expected_request_fingerprint != str(run.request_fingerprint):
        raise ValueError("backtest request fingerprint is not reproducible")
    parameter_fingerprint = canonical_fingerprint(dict(request_payload.get("parameter_set") or {}))
    if parameter_fingerprint != spec.parameter_fingerprint:
        raise ValueError("backtest parameters differ from frozen spec")
    execution_policy_payload = request_payload.get("execution_policy")
    if not isinstance(execution_policy_payload, Mapping):
        raise ValueError("stored execution policy is malformed")
    execution_policy_fingerprint = canonical_fingerprint(
        _drop_none_values(execution_policy_payload)
    )
    if execution_policy_fingerprint != spec.execution_policy_fingerprint:
        raise ValueError("stored execution policy differs from frozen spec")
    selector = request_payload.get("date_selector")
    if not isinstance(selector, Mapping) or (
        str(selector.get("start_date") or "") != spec.holdout_date_from
        or str(selector.get("end_date") or "") != spec.holdout_date_to
    ):
        raise ValueError("backtest date selector differs from frozen holdout")

    runner = ResearchBacktestRunner(repository)
    snapshot_manifest = _json_object(
        str(snapshot_row.partition_manifest_json),
        "partition_manifest_json",
    )
    artifacts_by_signal_date = snapshot_manifest.get("artifacts_by_signal_date")
    if not isinstance(artifacts_by_signal_date, Mapping):
        raise ValueError("snapshot artifact manifest is malformed")
    snapshot_signal_dates = tuple(sorted(str(value) for value in artifacts_by_signal_date))
    if len(snapshot_signal_dates) != spec.expected_signal_days:
        raise ValueError("snapshot signal-date count differs from frozen holdout")
    if canonical_fingerprint(list(snapshot_signal_dates)) != spec.signal_dates_fingerprint:
        raise ValueError("snapshot signal-date population differs from frozen holdout")
    normalized_execution_policy = _drop_none_values(execution_policy_payload)
    entry_policy = normalized_execution_policy.get("entry") or {}
    stock_metadata_temporality = runner.snapshot_stock_metadata_temporality(
        str(run.snapshot_id)
    )
    population = HoldoutPopulationIdentity.build(
        snapshot={
            "snapshot_id": str(run.snapshot_id),
            "snapshot_manifest_hash": str(snapshot_row.manifest_hash),
            "stock_metadata_temporality": stock_metadata_temporality,
        },
        actual_dates=snapshot_signal_dates,
        universe=(
            tuple(str(value) for value in (request_payload.get("target_codes") or ()))
            or ("__full_snapshot_universe__",)
        ),
        label_policy={
            "entry_lookahead_days": int(entry_policy.get("max_entry_trade_days") or 0),
            "horizon_days": int(normalized_execution_policy.get("horizon_days") or 0),
        },
        execution_policy=normalized_execution_policy,
    )
    claim_experiment_id = _quality_claim_experiment_id(spec)
    repository.require_holdout_claim(
        holdout_fingerprint=population.population_fingerprint,
        experiment_id=claim_experiment_id,
    )

    bundle = runner.load_snapshot_bundle(str(run.snapshot_id))
    input_manifest_hash = str(bundle.snapshot.input_manifest_hash)
    if str(run.snapshot_id) != f"bts_{input_manifest_hash}":
        raise ValueError("backtest snapshot identity does not match verified artifacts")

    result_payload = _json_object(str(run.result_json), "result_json")
    signal_dates = tuple(str(value) for value in (result_payload.get("signal_dates") or ()))
    if signal_dates != snapshot_signal_dates or signal_dates != bundle.signal_dates:
        raise ValueError("backtest result dates differ from verified snapshot")
    output_evidence_fingerprint = repository.output_evidence_fingerprint(backtest_id)
    if result_payload.get("output_evidence_fingerprint") != output_evidence_fingerprint:
        raise ValueError("completed backtest output evidence fingerprint differs")
    trades = tuple(
        _trade_from_json(str(row.trade_json))
        for row in db.scalars(
            select(BacktestTrade)
            .where(BacktestTrade.backtest_id == backtest_id)
            .order_by(BacktestTrade.signal_date, BacktestTrade.code, BacktestTrade.trade_id)
        ).all()
    )
    from app.services.backtest.strategy_profile import quality_evidences_for_run

    provenance = FrozenQualityProvenance(
        backtest_id=str(run.backtest_id),
        snapshot_id=str(run.snapshot_id),
        input_manifest_hash=input_manifest_hash,
        snapshot_manifest_hash=str(snapshot_row.manifest_hash),
        execution_version=str(run.execution_version),
        request_fingerprint=str(run.request_fingerprint),
        strategy_version=str(run.strategy_version),
        execution_policy_fingerprint=str(run.execution_policy_fingerprint),
        parameter_fingerprint=parameter_fingerprint,
        holdout_population_fingerprint=population.population_fingerprint,
        claim_experiment_id=claim_experiment_id,
        output_evidence_fingerprint=output_evidence_fingerprint,
        stock_metadata_temporality=stock_metadata_temporality,
    )
    return _evaluate_frozen_quality_holdout(
        quality_evidences_for_run(db, run, trades),
        signal_dates=signal_dates,
        spec=spec,
        config=config,
        cost_policy=cost_policy,
        provenance=provenance,
    )


def _evaluate_frozen_quality_holdout(
    evidences: Iterable[StrategyTradeEvidence],
    *,
    signal_dates: Iterable[str],
    spec: FrozenQualityValidationSpec,
    config: StrategyQualityConfig,
    cost_policy: QualityCostPolicy,
    provenance: FrozenQualityProvenance,
) -> dict[str, Any]:
    """Evaluate one pre-registered candidate without granting production status."""

    if config.strategy_id != spec.strategy_id:
        raise ValueError("quality config strategy_id differs from frozen spec")
    actual_quality_fingerprint = quality_policy_fingerprint(config, cost_policy)
    if actual_quality_fingerprint != spec.quality_fingerprint:
        raise ValueError("quality policy differs from frozen spec")
    ordered_dates = tuple(sorted(set(str(value) for value in signal_dates)))
    if not ordered_dates:
        raise ValueError("frozen holdout has no signal dates")
    if len(ordered_dates) != spec.expected_signal_days:
        raise ValueError("signal-date count differs from frozen holdout")
    actual_signal_dates_fingerprint = canonical_fingerprint(list(ordered_dates))
    if actual_signal_dates_fingerprint != spec.signal_dates_fingerprint:
        raise ValueError("signal-date population differs from frozen holdout")
    if ordered_dates[0] < spec.holdout_date_from or ordered_dates[-1] > spec.holdout_date_to:
        raise ValueError("signal dates fall outside the frozen holdout")

    evidence_tuple = tuple(evidences)
    if any(item.trade.signal_date not in frozenset(ordered_dates) for item in evidence_tuple):
        raise ValueError("trade evidence falls outside the frozen signal-date population")

    profile = compute_quality_profile(evidence_tuple, config, cost_policy)
    repriced_evidences = reprice_quality_evidences(evidence_tuple, cost_policy)
    gate = apply_quality_gate(repriced_evidences, config)
    represented_regimes = sorted({item.regime for item in gate.selected if item.regime != "unknown"})
    folds = _fold_evidence(
        gate.selected,
        ordered_dates,
        fold_count=spec.acceptance.fold_count,
    )
    positive_fold_count = sum(1 for item in folds if item["positive"])
    raw = profile["raw_metrics"]
    gated = profile["gated_metrics"]
    gates: dict[str, bool] = {
        "point_in_time_stock_metadata": provenance.stock_metadata_temporality == "point_in_time",
        "minimum_executed_trades": int(gated["sample_count"]) >= spec.acceptance.min_executed_trades,
        "minimum_signal_days": int(gated["signal_days"]) >= spec.acceptance.min_signal_days,
        "minimum_represented_regimes": len(represented_regimes) >= spec.acceptance.min_represented_regimes,
        "positive_average_net_return": (
            gated.get("avg_return") is not None
            and float(gated["avg_return"]) > spec.acceptance.min_average_net_return_pct
        ),
        "minimum_profit_factor": (
            _profit_factor_exceeds(gated, spec.acceptance.min_profit_factor)
        ),
        "max_drawdown_no_worse_than_raw": (
            float(gated["max_drawdown_pct"]) <= float(raw["max_drawdown_pct"])
        ),
        "fold_stability": positive_fold_count >= spec.acceptance.min_positive_folds,
    }
    historically_qualified = all(gates.values())
    invalid_evidence = not gates["point_in_time_stock_metadata"]
    artifact: dict[str, Any] = {
        "schema_version": FROZEN_QUALITY_VALIDATION_VERSION,
        "spec_fingerprint": spec.fingerprint,
        "signal_dates_fingerprint": actual_signal_dates_fingerprint,
        "provenance": provenance.canonical_payload(),
        "status": (
            "INVALID_EVIDENCE"
            if invalid_evidence
            else "HISTORICALLY_QUALIFIED" if historically_qualified else "FALSIFIED"
        ),
        "historically_qualified": historically_qualified,
        "production_release_ready": False,
        "production_blocker": (
            "point_in_time_stock_metadata_required"
            if invalid_evidence
            else "forward_canary_required"
        ),
        "holdout": {
            "date_from": ordered_dates[0],
            "date_to": ordered_dates[-1],
            "signal_days": len(ordered_dates),
        },
        "acceptance_gates": gates,
        "failed_gates": [name for name, passed in gates.items() if not passed],
        "represented_regimes": represented_regimes,
        "positive_fold_count": positive_fold_count,
        "folds": folds,
        "raw_metrics": raw,
        "gated_metrics": gated,
        "gate": profile["gate"],
        "cost_model": profile["cost_model"],
    }
    artifact["evidence_fingerprint"] = canonical_fingerprint(artifact)
    return artifact


def _fold_evidence(
    selected_evidences: tuple[StrategyTradeEvidence, ...],
    signal_dates: tuple[str, ...],
    *,
    fold_count: int,
) -> list[dict[str, Any]]:
    result = []
    for index in range(fold_count):
        start = len(signal_dates) * index // fold_count
        end = len(signal_dates) * (index + 1) // fold_count
        dates = signal_dates[start:end]
        date_set = frozenset(dates)
        metrics = _metrics_payload(
            item.trade for item in selected_evidences if item.trade.signal_date in date_set
        )
        positive = bool(
            metrics.get("sample_count")
            and metrics.get("avg_return") is not None
            and float(metrics["avg_return"]) > 0
            and _profit_factor_exceeds(metrics, 1.0)
        )
        result.append(
            {
                "fold": index + 1,
                "date_from": dates[0] if dates else None,
                "date_to": dates[-1] if dates else None,
                "signal_days": len(dates),
                "positive": positive,
                "metrics": dict(metrics),
            }
        )
    return result


def _metrics_payload(trades: Iterable[Any]) -> dict[str, Any]:
    normalized = tuple(trades)
    metrics = compute_metrics(normalized)
    return {
        "sample_count": metrics.executed_trade_count,
        "signal_days": len({trade.signal_date for trade in normalized if trade.executed}),
        "win_count": metrics.win_count,
        "loss_count": metrics.loss_count,
        "hit_rate": round(metrics.win_rate_pct / 100, 6) if metrics.executed_trade_count else None,
        "avg_return": round(metrics.average_net_return_pct, 4) if metrics.executed_trade_count else None,
        "profit_loss_ratio": (
            round(metrics.profit_loss_ratio, 4)
            if metrics.profit_loss_ratio is not None
            else None
        ),
        "total_return_pct": round(metrics.total_net_return_pct, 4),
        "max_drawdown_pct": round(metrics.max_drawdown_pct, 4),
    }


def _json_object(raw: str, field: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} is malformed") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{field} must be an object")
    return payload


def _drop_none_values(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _drop_none_values(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_drop_none_values(item) for item in value]
    return value


def _trade_from_json(raw: str) -> Trade:
    payload = _json_object(raw, "trade_json")
    try:
        return Trade(**payload)
    except (TypeError, ValueError) as exc:
        raise ValueError("trade_json is not a canonical Trade") from exc


def _profit_factor_exceeds(metrics: Mapping[str, Any], threshold: float) -> bool:
    value = metrics.get("profit_loss_ratio")
    if value is not None:
        return float(value) > threshold
    return int(metrics.get("win_count") or 0) > 0 and int(metrics.get("loss_count") or 0) == 0


def _quality_claim_experiment_id(spec: FrozenQualityValidationSpec) -> str:
    return f"strategy-quality:{spec.fingerprint[:48]}"


__all__ = [
    "FROZEN_QUALITY_VALIDATION_VERSION",
    "FrozenQualityAcceptance",
    "FrozenQualityProvenance",
    "FrozenQualityValidationSpec",
    "evaluate_frozen_quality_backtest",
    "quality_policy_fingerprint",
]
