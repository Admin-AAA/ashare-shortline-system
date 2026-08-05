"""Trade-by-trade execution parity and shadow-threshold evaluation.

This module is read-only and side-effect-free.  It compares appendable source
(old authoritative) and candidate (canonical shadow) payloads, attributes every
field difference, and evaluates a preregistered Shadow_Threshold_Manifest.  It
never changes a read path, policy, environment state, or activation state.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from enum import StrEnum
from typing import Any, Iterable, Mapping

from app.engine.canonical import CanonicalValue, canonical_fingerprint
from app.governance.manifests import SHADOW_THRESHOLD_KIND, validate_manifest_payload


_PARITY_FIELD_BY_METRIC = {
    "entry_date_mismatch_count": "entry_date",
    "entry_price_mismatch_count": "entry_price",
    "exit_date_mismatch_count": "exit_date",
    "exit_price_mismatch_count": "exit_price",
    "cost_mismatch_count": "cost_pct",
    "position_size_mismatch_count": "position_size",
    "exit_reason_mismatch_count": "exit_reason",
    "net_return_mismatch_count": "net_return_pct",
}
_REQUIRED_PAYLOAD_FIELDS = frozenset(_PARITY_FIELD_BY_METRIC.values()) | {
    "entry_triggered",
    "gross_return_pct",
}


class ShadowParityVerdict(StrEnum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"

_REASON_ALIASES = {
    "stop_loss": "stop_loss_hit",
    "take_profit": "take_profit_hit",
    "horizon": "horizon_close",
}
_NUMERIC_PARITY_FIELDS = frozenset(
    {"entry_price", "exit_price", "gross_return_pct", "cost_pct", "position_size", "net_return_pct"}
)
_DECIMAL_QUANTUM = Decimal("0.00000001")


def _normalized_parity_value(field_name: str, value: Any) -> Any:
    if field_name == "exit_reason" and isinstance(value, str):
        return _REASON_ALIASES.get(value, value)
    if field_name in _NUMERIC_PARITY_FIELDS and value is not None:
        try:
            return Decimal(str(value)).quantize(_DECIMAL_QUANTUM, rounding=ROUND_HALF_EVEN)
        except (InvalidOperation, ValueError, TypeError):
            return value
    return value


def _parity_values_equal(field_name: str, source: Any, candidate: Any) -> bool:
    return _normalized_parity_value(field_name, source) == _normalized_parity_value(
        field_name, candidate
    )
    PASS = "PASS"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class ExecutionParityDifference(CanonicalValue):
    metric_id: str
    field_path: str
    source_value: Any
    candidate_value: Any


@dataclass(frozen=True, slots=True)
class ExecutionParityObservation(CanonicalValue):
    recommendation_identity: str
    source_snapshot_id: str
    execution_policy_fingerprint: str
    code_revision: str
    source_adapter_role: str
    candidate_adapter_role: str
    source_payload_fingerprint: str
    candidate_payload_fingerprint: str
    differences: tuple[ExecutionParityDifference, ...]
    candidate_t_plus_one_same_day_exit: bool

    @property
    def matches(self) -> bool:
        return not self.differences and not self.candidate_t_plus_one_same_day_exit


@dataclass(frozen=True, slots=True)
class ShadowParityDecision(CanonicalValue):
    manifest_id: str
    sample_count: int
    verdict: ShadowParityVerdict
    metric_values: tuple[tuple[str, float], ...]
    blocker_reasons: tuple[str, ...]
    old_projection_remains_authoritative: bool = True
    environment_acceptance_state: str = "BLOCKED"


def _validated_payload(payload: Mapping[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{name} payload must be a mapping")
    missing = sorted(_REQUIRED_PAYLOAD_FIELDS - set(payload))
    if missing:
        raise ValueError(f"{name} payload missing parity fields: {missing}")
    return {field: payload.get(field) for field in sorted(_REQUIRED_PAYLOAD_FIELDS)}


def compare_execution_payloads(
    *,
    recommendation_identity: str,
    source_snapshot_id: str,
    execution_policy_fingerprint: str,
    code_revision: str,
    source_adapter_role: str,
    candidate_adapter_role: str,
    source_payload: Mapping[str, Any],
    candidate_payload: Mapping[str, Any],
    candidate_t_plus_one: bool,
) -> ExecutionParityObservation:
    """Compare one old/candidate trade pair with stable field attribution."""
    source = _validated_payload(source_payload, "source")
    candidate = _validated_payload(candidate_payload, "candidate")
    differences = tuple(
        ExecutionParityDifference(
            metric_id=metric_id,
            field_path=f"$.{field_name}",
            source_value=source[field_name],
            candidate_value=candidate[field_name],
        )
        for metric_id, field_name in _PARITY_FIELD_BY_METRIC.items()
        if not _parity_values_equal(field_name, source[field_name], candidate[field_name])
    )
    same_day = bool(
        candidate_t_plus_one
        and candidate.get("entry_triggered")
        and candidate.get("entry_date") is not None
        and candidate.get("entry_date") == candidate.get("exit_date")
    )
    return ExecutionParityObservation(
        recommendation_identity=str(recommendation_identity),
        source_snapshot_id=str(source_snapshot_id),
        execution_policy_fingerprint=str(execution_policy_fingerprint),
        code_revision=str(code_revision),
        source_adapter_role=str(source_adapter_role),
        candidate_adapter_role=str(candidate_adapter_role),
        source_payload_fingerprint=canonical_fingerprint(source),
        candidate_payload_fingerprint=canonical_fingerprint(candidate),
        differences=differences,
        candidate_t_plus_one_same_day_exit=same_day,
    )


def _compare(value: float, comparator: str, threshold: float) -> bool:
    return {
        "<": value < threshold,
        "<=": value <= threshold,
        "==": value == threshold,
        ">=": value >= threshold,
        ">": value > threshold,
    }[comparator]


def evaluate_shadow_threshold_manifest(
    *,
    manifest_id: str,
    manifest_payload: Mapping[str, Any],
    observations: Iterable[ExecutionParityObservation],
) -> ShadowParityDecision:
    """Evaluate all required rules; any failure keeps old projection authoritative."""
    validate_manifest_payload(SHADOW_THRESHOLD_KIND, manifest_payload)
    rows = tuple(observations)
    metric_values = {
        metric_id: float(
            sum(
                1
                for row in rows
                for difference in row.differences
                if difference.metric_id == metric_id
            )
        )
        for metric_id in _PARITY_FIELD_BY_METRIC
    }
    metric_values["t_plus_one_same_day_exit_count"] = float(
        sum(1 for row in rows if row.candidate_t_plus_one_same_day_exit)
    )

    blockers: list[str] = []
    for rule in manifest_payload["rules"]:
        metric_id = str(rule["metric_id"])
        minimum_sample = int(rule["minimum_sample"])
        if len(rows) < minimum_sample:
            blockers.append(
                f"INSUFFICIENT_SAMPLE:{metric_id}:{len(rows)}<{minimum_sample}"
            )
            continue
        if metric_id not in metric_values:
            blockers.append(f"UNREGISTERED_METRIC:{metric_id}")
            continue
        value = metric_values[metric_id]
        threshold = float(rule["threshold_decimal"])
        if not _compare(value, str(rule["comparator"]), threshold):
            blockers.append(
                f"SHADOW_THRESHOLD_FAILED:{metric_id}:{value}{rule['comparator']}{threshold}"
            )

    verdict = ShadowParityVerdict.BLOCKED if blockers else ShadowParityVerdict.PASS
    return ShadowParityDecision(
        manifest_id=manifest_id,
        sample_count=len(rows),
        verdict=verdict,
        metric_values=tuple(sorted(metric_values.items())),
        blocker_reasons=tuple(blockers),
    )


__all__ = [
    "ExecutionParityDifference",
    "ExecutionParityObservation",
    "ShadowParityDecision",
    "ShadowParityVerdict",
    "compare_execution_payloads",
    "evaluate_shadow_threshold_manifest",
]
