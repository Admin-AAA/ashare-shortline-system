"""Canonical Champion/Challenger experiment over one immutable snapshot.

This is the formal self-improve backtest interface.  It owns experiment
orchestration and evidence assembly while delegating strategy execution,
simulation, persistence, validation, and publication policy to their existing
deep modules.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
from typing import Any, Mapping, Sequence

from app.engine.backtest import (
    BacktestRequest,
    Trade,
    canonical_fingerprint,
)
from app.engine.simulation import compute_metrics
from app.governance.holdout import HoldoutPopulationIdentity
from app.services.backtest.challenger_decision import (
    PerformanceSnapshot,
    evaluate_challenger,
)


EVIDENCE_SCHEMA_VERSION = 2
DECISION_POLICY = "canonical_strategy_oos_release:v2"
SPLIT_POLICY = "purged_chronological_60_20_20:v2"
UNBOUNDED_PROFIT_FACTOR = 999.0
MIN_OOS_EXECUTED_TRADES = 100
MIN_OOS_EXECUTED_SIGNAL_DAYS = 20
MIN_OOS_PROFIT_FACTOR = 1.10


class CanonicalExperimentValidationError(ValueError):
    """The two experiment arms cannot produce comparable formal evidence."""

    code = "canonical_experiment_validation_error"

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})


@dataclass(frozen=True)
class CanonicalExperimentSpec:
    """One immutable hypothesis and its Champion/Challenger requests."""

    experiment_id: str
    hypothesis: str
    champion: BacktestRequest
    challenger: BacktestRequest

    def __post_init__(self) -> None:
        if not isinstance(self.experiment_id, str) or not self.experiment_id.strip():
            raise CanonicalExperimentValidationError("experiment_id must be non-empty")
        if len(self.experiment_id) > 80:
            raise CanonicalExperimentValidationError("experiment_id must be at most 80 characters")
        if not isinstance(self.hypothesis, str) or not self.hypothesis.strip():
            raise CanonicalExperimentValidationError("hypothesis must be non-empty")
        if not isinstance(self.champion, BacktestRequest) or not isinstance(
            self.challenger, BacktestRequest
        ):
            raise CanonicalExperimentValidationError(
                "champion and challenger must be BacktestRequest values"
            )
        object.__setattr__(self, "experiment_id", self.experiment_id.strip())
        object.__setattr__(self, "hypothesis", self.hypothesis.strip())


class CanonicalBacktestExperiment:
    """Run, validate, decide, and persist one comparable two-arm experiment."""

    def __init__(self, repository: Any, runner: Any) -> None:
        self._repository = repository
        self._runner = runner

    def run(self, spec: CanonicalExperimentSpec | Any) -> dict[str, Any]:
        # Import lazily: policy_experiment reuses the canonical performance
        # adapter, so a top-level import would create a cycle.
        from app.services.backtest.policy_experiment import (
            CanonicalPolicyExperimentSpec,
            ProductionPolicyBacktestRunner,
        )

        if isinstance(spec, CanonicalPolicyExperimentSpec):
            return ProductionPolicyBacktestRunner(self._repository).run(spec)
        if not isinstance(spec, CanonicalExperimentSpec):
            raise CanonicalExperimentValidationError(
                "spec must be CanonicalExperimentSpec or CanonicalPolicyExperimentSpec"
            )
        champion = self._runner.resolve_request(spec.champion)
        challenger = self._runner.resolve_request(spec.challenger)
        _validate_comparable_requests(champion, challenger)

        champion_configuration_fingerprint = _configuration_fingerprint(champion)
        challenger_configuration_fingerprint = _configuration_fingerprint(challenger)
        champion_result = self._runner.run(
            replace(
                champion,
                experiment_id=spec.experiment_id,
                baseline_backtest_id=None,
                force_rerun=False,
            )
        )
        champion_payload = _completed_run_payload(champion_result, arm="champion")
        snapshot_id = _required_text(champion_result, "snapshot_id", arm="champion")
        stock_metadata_temporality = self._runner.snapshot_stock_metadata_temporality(
            snapshot_id
        )
        champion_backtest_id = _required_text(champion_result, "backtest_id", arm="champion")
        signal_dates = _signal_dates(champion_payload, arm="champion")
        population_split = _chronological_split(signal_dates)
        purge_signal_days = (
            champion.execution_policy.entry_lookahead_days
            + champion.execution_policy.horizon_days
        )
        split = _purged_chronological_split(
            signal_dates,
            purge_signal_days=purge_signal_days,
        )
        holdout_fingerprint = _holdout_population_fingerprint(
            snapshot_id=snapshot_id,
            request=champion,
            out_of_sample_dates=split["out_of_sample"],
            stock_metadata_temporality=stock_metadata_temporality,
        )

        experiment = self._repository.create_experiment(
            experiment_id=spec.experiment_id,
            hypothesis=spec.hypothesis,
            baseline_backtest_id=champion_backtest_id,
            snapshot_id=snapshot_id,
            execution_policy_fingerprint=champion.execution_policy.fingerprint(),
            split_json=population_split,
            split_hash=None,
            candidate_strategy_id=challenger.strategy_id,
            candidate_strategy_version=str(challenger.strategy_version),
            frozen_candidate_fingerprint=challenger_configuration_fingerprint,
            acceptance={
                "decision_policy": DECISION_POLICY,
                "split_policy": SPLIT_POLICY,
                "purge_signal_days": purge_signal_days,
                "decision_split": split,
                "holdout_fingerprint": holdout_fingerprint,
                "holdout_identity_policy": "server_derived_snapshot_population:v1",
                "release_gates": _release_gate_manifest(),
            },
        )
        self._repository.freeze_experiment(
            spec.experiment_id,
            frozen_candidate_fingerprint=challenger_configuration_fingerprint,
        )
        _commit(self._repository)

        try:
            self._repository.claim_holdout(
                holdout_fingerprint=holdout_fingerprint,
                experiment_id=spec.experiment_id,
                phase="untouched_holdout",
            )
            _commit(self._repository)
            challenger_result = self._runner.run_on_snapshot(
                replace(
                    challenger,
                    experiment_id=spec.experiment_id,
                    baseline_backtest_id=champion_backtest_id,
                    force_rerun=False,
                ),
                snapshot_id,
            )
            challenger_payload = _completed_run_payload(challenger_result, arm="challenger")
            _validate_shared_snapshot(challenger_result, snapshot_id)
            if _signal_dates(challenger_payload, arm="challenger") != signal_dates:
                raise CanonicalExperimentValidationError(
                    "challenger signal_dates differ from the frozen champion snapshot"
                )
        except Exception as exc:
            _persist_execution_failure(self._repository, experiment, exc)
            raise

        champion_arm = self._arm_evidence(
            result=champion_result,
            payload=champion_payload,
            configuration_fingerprint=champion_configuration_fingerprint,
            split=split,
        )
        challenger_arm = self._arm_evidence(
            result=challenger_result,
            payload=challenger_payload,
            configuration_fingerprint=challenger_configuration_fingerprint,
            split=split,
        )
        truth_validation = _truth_validation(champion_arm, challenger_arm)
        if truth_validation["passed"]:
            decision = _oos_decision(
                champion_arm,
                challenger_arm,
                stock_metadata_temporality=stock_metadata_temporality,
            )
            terminal_status = "completed"
        else:
            decision = {
                "decision": "INVALID_EVIDENCE",
                "should_publish": False,
                "improvements": [],
                "regressions": [],
                "deltas": {},
            }
            terminal_status = "failed"

        artifact: dict[str, Any] = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "experiment_id": spec.experiment_id,
            "status": terminal_status,
            "hypothesis": spec.hypothesis,
            "shared_snapshot_id": snapshot_id,
            "split": split,
            "population_split": population_split,
            "decision_scope": "out_of_sample",
            "decision_policy": DECISION_POLICY,
            "split_policy": SPLIT_POLICY,
            "purge_signal_days": purge_signal_days,
            "holdout_fingerprint": holdout_fingerprint,
            "holdout_identity_policy": "server_derived_snapshot_population:v1",
            "stock_metadata_temporality": stock_metadata_temporality,
            "release_gates": _release_gate_manifest(),
            "arms": {
                "champion": champion_arm,
                "challenger": challenger_arm,
            },
            "truth_validation": truth_validation,
            "decision": decision,
        }
        artifact["evidence_fingerprint"] = canonical_fingerprint(
            _semantic_evidence_payload(artifact)
        )
        self._repository.complete_experiment(
            spec.experiment_id,
            status=terminal_status,
            conclusion=artifact,
            snapshot_id=str(experiment.snapshot_id),
            execution_policy_fingerprint=str(experiment.execution_policy_fingerprint),
            split_hash=str(experiment.split_hash),
            frozen_candidate_fingerprint=str(experiment.frozen_candidate_fingerprint),
        )
        _commit(self._repository)
        return artifact

    def _arm_evidence(
        self,
        *,
        result: Mapping[str, Any],
        payload: Mapping[str, Any],
        configuration_fingerprint: str,
        split: Mapping[str, list[str]],
    ) -> dict[str, Any]:
        backtest_id = _required_text(result, "backtest_id", arm="result")
        trades = tuple(_trade_from_row(row) for row in self._repository.list_trades(backtest_id))
        metrics = compute_metrics(trades).canonical_payload()
        split_trades = {
            name: tuple(trade for trade in trades if trade.signal_date in frozenset(dates))
            for name, dates in split.items()
        }
        split_metrics = {
            name: compute_metrics(values).canonical_payload()
            for name, values in split_trades.items()
        }
        validation = payload.get("cross_validation")
        if not isinstance(validation, Mapping):
            validation = {"passed": False, "error": "missing_cross_validation"}
        return {
            "backtest_id": backtest_id,
            "strategy_id": _required_text(result, "strategy_id", arm="result"),
            "strategy_version": _required_text(result, "strategy_version", arm="result"),
            "configuration_fingerprint": configuration_fingerprint,
            "metrics": metrics,
            "split_metrics": split_metrics,
            "split_executed_signal_days": {
                name: len({trade.signal_date for trade in values if trade.executed})
                for name, values in split_trades.items()
            },
            "cross_validation": dict(validation),
        }


def _validate_comparable_requests(champion: BacktestRequest, challenger: BacktestRequest) -> None:
    comparable = {
        "date_selector": (champion.date_selector, challenger.date_selector),
        "target_codes": (champion.target_codes, challenger.target_codes),
        "data_policy": (champion.data_policy, challenger.data_policy),
        "execution_policy": (champion.execution_policy, challenger.execution_policy),
    }
    drift = [name for name, (left, right) in comparable.items() if left != right]
    if drift:
        raise CanonicalExperimentValidationError(
            "champion and challenger must share input and execution semantics: "
            + ", ".join(drift),
            details={"drift": drift},
        )


def _configuration_fingerprint(request: BacktestRequest) -> str:
    return canonical_fingerprint(
        {
            "schema_version": request.schema_version,
            "strategy_id": request.strategy_id,
            "strategy_version": request.strategy_version,
            "date_selector": request.date_selector,
            "target_codes": request.target_codes,
            "data_policy": request.data_policy,
            "execution_policy": request.execution_policy,
            "parameter_set": request.parameter_set,
        }
    )


def _completed_run_payload(result: Mapping[str, Any], *, arm: str) -> Mapping[str, Any]:
    if result.get("status") != "completed":
        raise CanonicalExperimentValidationError(
            f"{arm} run did not complete",
            details={"arm": arm, "status": result.get("status")},
        )
    payload = result.get("result")
    if not isinstance(payload, Mapping) or payload.get("status") != "completed":
        raise CanonicalExperimentValidationError(
            f"{arm} run has no completed canonical result payload"
        )
    return payload


def _required_text(value: Mapping[str, Any], field: str, *, arm: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result:
        raise CanonicalExperimentValidationError(f"{arm} is missing {field}")
    return result


def _signal_dates(payload: Mapping[str, Any], *, arm: str) -> tuple[str, ...]:
    dates = payload.get("signal_dates")
    if not isinstance(dates, Sequence) or isinstance(dates, (str, bytes)):
        raise CanonicalExperimentValidationError(f"{arm} has no signal_dates")
    normalized = tuple(str(day) for day in dates)
    if normalized != tuple(sorted(set(normalized))):
        raise CanonicalExperimentValidationError(f"{arm} signal_dates must be sorted and unique")
    return normalized


def _chronological_split(signal_dates: tuple[str, ...]) -> dict[str, list[str]]:
    if len(signal_dates) < 3:
        raise CanonicalExperimentValidationError(
            "canonical experiment requires at least three signal dates"
        )
    train_end = max(1, int(len(signal_dates) * 0.60))
    validation_end = max(train_end + 1, int(len(signal_dates) * 0.80))
    validation_end = min(validation_end, len(signal_dates) - 1)
    return {
        "train": list(signal_dates[:train_end]),
        "validation": list(signal_dates[train_end:validation_end]),
        "out_of_sample": list(signal_dates[validation_end:]),
    }


def _purged_chronological_split(
    signal_dates: tuple[str, ...],
    *,
    purge_signal_days: int,
) -> dict[str, list[str]]:
    population = _chronological_split(signal_dates)
    train_end = len(population["train"])
    validation_end = train_end + len(population["validation"])
    split = {
        "train": population["train"],
        "validation": list(signal_dates[train_end + purge_signal_days : validation_end]),
        "out_of_sample": list(signal_dates[validation_end + purge_signal_days :]),
    }
    if any(not dates for dates in split.values()):
        raise CanonicalExperimentValidationError(
            "purge/embargo left an empty canonical experiment partition",
            details={
                "signal_date_count": len(signal_dates),
                "purge_signal_days": purge_signal_days,
            },
        )
    return split


def _validate_shared_snapshot(result: Mapping[str, Any], expected_snapshot_id: str) -> None:
    actual = result.get("snapshot_id")
    if actual != expected_snapshot_id:
        raise CanonicalExperimentValidationError(
            "challenger did not execute on the champion snapshot",
            details={"expected_snapshot_id": expected_snapshot_id, "actual_snapshot_id": actual},
        )


def _trade_from_row(row: Any) -> Trade:
    raw = row.get("trade_json") if isinstance(row, Mapping) else getattr(row, "trade_json", None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CanonicalExperimentValidationError("stored trade_json is malformed") from exc
    if not isinstance(raw, Mapping):
        raise CanonicalExperimentValidationError("stored trade_json must be an object")
    try:
        return Trade(**dict(raw))
    except (TypeError, ValueError) as exc:
        raise CanonicalExperimentValidationError("stored trade_json is not a canonical Trade") from exc


def _truth_validation(
    champion_arm: Mapping[str, Any], challenger_arm: Mapping[str, Any]
) -> dict[str, Any]:
    validators = {
        "champion_vectorbt": champion_arm["cross_validation"].get("passed") is True,
        "challenger_vectorbt": challenger_arm["cross_validation"].get("passed") is True,
    }
    return {"passed": all(validators.values()), "validators": validators}


def _oos_decision(
    champion_arm: Mapping[str, Any],
    challenger_arm: Mapping[str, Any],
    *,
    stock_metadata_temporality: str,
) -> dict[str, Any]:
    champion = champion_arm["split_metrics"]["out_of_sample"]
    challenger = challenger_arm["split_metrics"]["out_of_sample"]
    financial = evaluate_challenger(
        _performance_snapshot(champion),
        _performance_snapshot(challenger),
    ).to_dict()
    profit_factor = _performance_snapshot(challenger).profit_factor
    gates = {
        "point_in_time_stock_metadata": stock_metadata_temporality == "point_in_time",
        "minimum_oos_executed_trades": (
            int(challenger.get("executed_trade_count") or 0) >= MIN_OOS_EXECUTED_TRADES
        ),
        "minimum_oos_executed_signal_days": (
            int(challenger_arm["split_executed_signal_days"]["out_of_sample"])
            >= MIN_OOS_EXECUTED_SIGNAL_DAYS
        ),
        "positive_oos_average_net_return": (
            float(challenger.get("average_net_return_pct") or 0.0) > 0.0
        ),
        "minimum_oos_profit_factor": profit_factor > MIN_OOS_PROFIT_FACTOR,
        "oos_max_drawdown_no_worse_than_champion": (
            float(challenger.get("max_drawdown_pct") or 0.0)
            <= float(champion.get("max_drawdown_pct") or 0.0)
        ),
    }
    historically_qualified = bool(financial["should_publish"] and all(gates.values()))
    return {
        **financial,
        "decision": "FORWARD_CANDIDATE" if historically_qualified else "FALSIFIED",
        "should_publish": False,
        "historically_qualified": historically_qualified,
        "decision_scope": "out_of_sample",
        "production_blocker": (
            "forward_canary_required"
            if gates["point_in_time_stock_metadata"]
            else "point_in_time_stock_metadata_required"
        ),
        "gates": gates,
        "failed_gates": [name for name, passed in gates.items() if not passed],
        "improvements": list(financial["improvements"]),
        "regressions": list(financial["regressions"]),
    }


def _release_gate_manifest() -> dict[str, Any]:
    return {
        "min_oos_executed_trades": MIN_OOS_EXECUTED_TRADES,
        "min_oos_executed_signal_days": MIN_OOS_EXECUTED_SIGNAL_DAYS,
        "min_oos_average_net_return_pct": 0.0,
        "min_oos_profit_factor": MIN_OOS_PROFIT_FACTOR,
        "max_drawdown_rule": "challenger_no_worse_than_champion",
        "forward_canary_required": True,
    }


def _holdout_population_fingerprint(
    *,
    snapshot_id: str,
    request: BacktestRequest,
    out_of_sample_dates: Sequence[str],
    stock_metadata_temporality: str,
) -> str:
    identity = HoldoutPopulationIdentity.build(
        snapshot={
            "snapshot_id": snapshot_id,
            "stock_metadata_temporality": stock_metadata_temporality,
        },
        actual_dates=out_of_sample_dates,
        universe=(
            request.target_codes
            if request.target_codes
            else ("__full_snapshot_universe__",)
        ),
        label_policy={
            "entry_lookahead_days": request.execution_policy.entry_lookahead_days,
            "horizon_days": request.execution_policy.horizon_days,
        },
        execution_policy=request.execution_policy.canonical_payload(),
    )
    return identity.population_fingerprint


def _performance_snapshot(metrics: Mapping[str, Any]) -> PerformanceSnapshot:
    profit_factor = metrics.get("profit_loss_ratio")
    if profit_factor is None:
        profit_factor = (
            UNBOUNDED_PROFIT_FACTOR
            if int(metrics.get("win_count") or 0) > 0 and int(metrics.get("loss_count") or 0) == 0
            else 0.0
        )
    values = {
        "average_net_return_pct": metrics.get("average_net_return_pct"),
        "cumulative_net_return_pct": metrics.get("total_net_return_pct"),
        "win_rate_pct": metrics.get("win_rate_pct"),
        "profit_factor": profit_factor,
        # Domain metrics store drawdown as a positive magnitude.  The production
        # decision expects a less-negative signed drawdown to be an improvement.
        "max_drawdown_pct": -float(metrics.get("max_drawdown_pct") or 0.0),
    }
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        for value in values.values()
    ):
        raise CanonicalExperimentValidationError("backtest metrics are incomplete or non-finite")
    return PerformanceSnapshot(**{name: float(value) for name, value in values.items()})


def _semantic_cross_validation(value: Mapping[str, Any]) -> dict[str, Any]:
    semantic = {key: item for key, item in value.items() if key != "first_divergence"}
    divergence = value.get("first_divergence")
    if isinstance(divergence, Mapping):
        semantic["first_divergence"] = {
            key: item for key, item in divergence.items() if key != "trade_id"
        }
    else:
        semantic["first_divergence"] = divergence
    return semantic


def _semantic_evidence_payload(artifact: Mapping[str, Any]) -> dict[str, Any]:
    arms = artifact["arms"]
    return {
        "schema_version": artifact["schema_version"],
        "shared_snapshot_id": artifact["shared_snapshot_id"],
        "split": artifact["split"],
        "population_split": artifact["population_split"],
        "decision_scope": artifact["decision_scope"],
        "decision_policy": artifact["decision_policy"],
        "split_policy": artifact["split_policy"],
        "purge_signal_days": artifact["purge_signal_days"],
        "holdout_fingerprint": artifact["holdout_fingerprint"],
        "holdout_identity_policy": artifact["holdout_identity_policy"],
        "stock_metadata_temporality": artifact["stock_metadata_temporality"],
        "release_gates": artifact["release_gates"],
        "arms": {
            name: {
                "strategy_id": arm["strategy_id"],
                "strategy_version": arm["strategy_version"],
                "configuration_fingerprint": arm["configuration_fingerprint"],
                "metrics": arm["metrics"],
                "split_metrics": arm["split_metrics"],
                "split_executed_signal_days": arm["split_executed_signal_days"],
                "cross_validation": _semantic_cross_validation(arm["cross_validation"]),
            }
            for name, arm in arms.items()
        },
        "truth_validation": artifact["truth_validation"],
        "decision": artifact["decision"],
    }


def _persist_execution_failure(repository: Any, experiment: Any, exc: Exception) -> None:
    repository.complete_experiment(
        str(experiment.experiment_id),
        status="failed",
        conclusion={
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "status": "failed",
            "error": {
                "code": str(getattr(exc, "code", "canonical_experiment_execution_failed")),
                "message": str(exc),
            },
        },
        snapshot_id=str(experiment.snapshot_id),
        execution_policy_fingerprint=str(experiment.execution_policy_fingerprint),
        split_hash=str(experiment.split_hash),
        frozen_candidate_fingerprint=str(experiment.frozen_candidate_fingerprint),
    )
    _commit(repository)


def _commit(repository: Any) -> None:
    session = getattr(repository, "session", None)
    if session is not None:
        session.commit()


__all__ = [
    "CanonicalBacktestExperiment",
    "CanonicalExperimentSpec",
    "CanonicalExperimentValidationError",
]
