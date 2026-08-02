"""Canonical production-policy experiment over frozen scored-candidate days."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import json
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from app.engine.backtest import (
    BarInput,
    ExecutionPolicy,
    RecommendationInput,
    Trade,
    canonical_fingerprint,
)
from app.engine.production_policy import (
    ProductionSelectionPolicy,
    SelectionPolicyContext,
    evaluate_selection_policy,
)
from app.engine.simulation import compute_metrics, simulate
from app.services.backtest.release_policy import DEFAULT_RELEASE_POLICY
from app.services.backtest.vectorbt_validation import cross_validate_trades


POLICY_EVIDENCE_SCHEMA_VERSION = 2
RELEASE_POLICY_VERSION = DEFAULT_RELEASE_POLICY.identity
SPLIT_POLICY_VERSION = "chronological_60_20_20_purged:v2"


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True)
class ScoredCandidateDay:
    signal_date: str
    market_snapshot: Mapping[str, Any]
    cards: tuple[Mapping[str, Any], ...]
    outcome_bars: Mapping[str, tuple[BarInput, ...]]

    def __post_init__(self) -> None:
        if not self.signal_date:
            raise ValueError("signal_date is required")
        cards = tuple(
            _freeze_json(json.loads(json.dumps(dict(card), ensure_ascii=False, allow_nan=False)))
            for card in self.cards
        )
        bars = {
            str(code): tuple(values)
            for code, values in self.outcome_bars.items()
        }
        if not all(
            isinstance(bar, BarInput)
            for values in bars.values()
            for bar in values
        ):
            raise TypeError("outcome_bars must contain BarInput values")
        object.__setattr__(self, "market_snapshot", _freeze_json(dict(self.market_snapshot)))
        object.__setattr__(self, "cards", cards)
        object.__setattr__(self, "outcome_bars", MappingProxyType(bars))

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "signal_date": self.signal_date,
            "market_snapshot": _thaw_json(self.market_snapshot),
            "cards": [_thaw_json(card) for card in self.cards],
            "outcome_bars": {
                code: [bar.canonical_payload() for bar in values]
                for code, values in sorted(self.outcome_bars.items())
            },
        }


@dataclass(frozen=True)
class ScoredCandidateSnapshot:
    source: str
    days: tuple[ScoredCandidateDay, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("candidate snapshot source is required")
        days = tuple(self.days)
        dates = tuple(day.signal_date for day in days)
        if not days or dates != tuple(sorted(set(dates))):
            raise ValueError("candidate snapshot days must be non-empty, sorted and unique")
        object.__setattr__(self, "days", days)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "days": [day.canonical_payload() for day in self.days],
        }

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.canonical_payload())

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ScoredCandidateSnapshot":
        if not isinstance(payload, Mapping):
            raise ValueError("candidate snapshot payload must be an object")
        raw_days = payload.get("days")
        if not isinstance(raw_days, Sequence) or isinstance(raw_days, (str, bytes)):
            raise ValueError("candidate snapshot days must be an array")
        days = []
        for raw_day in raw_days:
            if not isinstance(raw_day, Mapping):
                raise ValueError("candidate snapshot day must be an object")
            raw_outcomes = raw_day.get("outcome_bars") or {}
            if not isinstance(raw_outcomes, Mapping):
                raise ValueError("outcome_bars must be an object")
            days.append(
                ScoredCandidateDay(
                    signal_date=str(raw_day.get("signal_date") or ""),
                    market_snapshot=dict(raw_day.get("market_snapshot") or {}),
                    cards=tuple(raw_day.get("cards") or ()),
                    outcome_bars={
                        str(code): tuple(BarInput(**dict(bar)) for bar in bars)
                        for code, bars in raw_outcomes.items()
                    },
                )
            )
        return cls(
            source=str(payload.get("source") or ""),
            days=tuple(days),
            schema_version=int(payload.get("schema_version") or 1),
        )


@dataclass(frozen=True)
class CanonicalPolicyExperimentSpec:
    experiment_id: str
    hypothesis: str
    candidate_snapshot: ScoredCandidateSnapshot
    champion_policy: ProductionSelectionPolicy
    challenger_policy: ProductionSelectionPolicy
    changed_rule_id: str
    score_line: float
    output_count: int
    execution_policy: ExecutionPolicy

    def __post_init__(self) -> None:
        if not self.experiment_id or not self.hypothesis:
            raise ValueError("experiment_id and hypothesis are required")
        if len(self.experiment_id) > 80:
            raise ValueError("experiment_id must be at most 80 characters")
        if self.champion_policy.fingerprint == self.challenger_policy.fingerprint:
            raise ValueError("champion and challenger policy must differ")
        expected = _changed_rule_ids(self.champion_policy, self.challenger_policy)
        if expected != (self.changed_rule_id,):
            raise ValueError(
                f"production-policy experiment must change exactly one rule: {expected}"
            )


@dataclass(frozen=True)
class PortfolioCoverageEvidence:
    eligible_days: int
    opportunity_days: int
    active_days: int
    executed_trades: int
    opportunity_capture_rate_pct: float
    abstain_days: int
    max_consecutive_opportunity_abstain_days: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible_days": self.eligible_days,
            "opportunity_days": self.opportunity_days,
            "active_days": self.active_days,
            "executed_trades": self.executed_trades,
            "opportunity_capture_rate_pct": self.opportunity_capture_rate_pct,
            "abstain_days": self.abstain_days,
            "max_consecutive_opportunity_abstain_days": self.max_consecutive_opportunity_abstain_days,
        }


class ProductionPolicyBacktestRunner:
    """Evaluate two selection policies on one immutable candidate snapshot."""

    def __init__(self, repository: Any | None = None) -> None:
        self._repository = repository

    def run(self, spec: CanonicalPolicyExperimentSpec) -> dict[str, Any]:
        try:
            split = _purged_split(
                tuple(day.signal_date for day in spec.candidate_snapshot.days),
                purge_days=(
                    spec.execution_policy.entry_lookahead_days
                    + spec.execution_policy.horizon_days
                ),
            )
        except ValueError as exc:
            artifact = {
                "schema_version": POLICY_EVIDENCE_SCHEMA_VERSION,
                "experiment_kind": "production_policy",
                "experiment_id": spec.experiment_id,
                "status": "invalid_evidence",
                "hypothesis": spec.hypothesis,
                "candidate_source": spec.candidate_snapshot.source,
                "candidate_snapshot_fingerprint": spec.candidate_snapshot.fingerprint,
                "champion_policy_fingerprint": spec.champion_policy.fingerprint,
                "challenger_policy_fingerprint": spec.challenger_policy.fingerprint,
                "changed_rule_id": spec.changed_rule_id,
                "split_policy": SPLIT_POLICY_VERSION,
                "decision_scope": "out_of_sample",
                "decision": {
                    "decision": "INVALID_EVIDENCE",
                    "should_publish": False,
                    "release_policy": RELEASE_POLICY_VERSION,
                    "failed_gates": ["nonempty_purged_partitions"],
                    "reason": str(exc),
                },
            }
            artifact["evidence_fingerprint"] = canonical_fingerprint(
                {key: value for key, value in artifact.items() if key not in {"experiment_id", "hypothesis"}}
            )
            return artifact
        champion, champion_records = self._run_arm(
            spec, spec.champion_policy, split
        )
        challenger, challenger_records = self._run_arm(
            spec, spec.challenger_policy, split
        )
        decision = DEFAULT_RELEASE_POLICY.evaluate(
            champion=champion,
            challenger=challenger,
            changed_rule_ids=_changed_rule_ids(
                spec.champion_policy, spec.challenger_policy
            ),
        )
        artifact: dict[str, Any] = {
            "schema_version": POLICY_EVIDENCE_SCHEMA_VERSION,
            "experiment_kind": "production_policy",
            "experiment_id": spec.experiment_id,
            "status": "completed",
            "hypothesis": spec.hypothesis,
            "candidate_source": spec.candidate_snapshot.source,
            "candidate_snapshot_fingerprint": spec.candidate_snapshot.fingerprint,
            "champion_policy_fingerprint": spec.champion_policy.fingerprint,
            "challenger_policy_fingerprint": spec.challenger_policy.fingerprint,
            "changed_rule_id": spec.changed_rule_id,
            "split_policy": SPLIT_POLICY_VERSION,
            "split": split,
            "decision_scope": "out_of_sample",
            "arms": {"champion": champion, "challenger": challenger},
            "coverage_evidence": {
                "champion": champion["coverage_evidence"],
                "challenger": challenger["coverage_evidence"],
            },
            "rule_verdict_summary": {
                "champion": champion["rule_verdict_summary"],
                "challenger": challenger["rule_verdict_summary"],
            },
            "decision": decision,
        }
        artifact["evidence_fingerprint"] = canonical_fingerprint(
            {key: value for key, value in artifact.items() if key not in {"experiment_id", "hypothesis"}}
        )
        if self._repository is not None:
            self._persist(
                spec,
                artifact,
                champion_records=champion_records,
                challenger_records=challenger_records,
            )
        return artifact

    def _run_arm(
        self,
        spec: CanonicalPolicyExperimentSpec,
        policy: ProductionSelectionPolicy,
        split: Mapping[str, list[str]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        trades: list[Trade] = []
        candidate_rows: list[dict[str, Any]] = []
        active_dates: set[str] = set()
        opportunity_dates: set[str] = set()
        verdict_summary: Counter[str] = Counter()
        for day in spec.candidate_snapshot.days:
            cards = _thaw_json(day.cards)
            if _is_opportunity_day(cards, spec.score_line):
                opportunity_dates.add(day.signal_date)
            result = evaluate_selection_policy(
                cards,
                SelectionPolicyContext(
                    score_line=spec.score_line,
                    output_count=spec.output_count,
                    market_snapshot=day.market_snapshot,
                    agent_available=False,
                ),
                policy,
            )
            verdict_summary.update(dict(result.verdict_summary))
            verdict_by_code = {item.code: item for item in result.verdicts}
            selected_codes = {
                str(card.get("code") or "") for card in result.recommendations
            }
            for card in cards:
                code = str(card.get("code") or "")
                if not code:
                    continue
                verdict = verdict_by_code[code]
                candidate_rows.append(
                    {
                        "signal_date": day.signal_date,
                        "code": code,
                        "decision_status": (
                            "recommended" if code in selected_codes else "rejected"
                        ),
                        "signal_json": dict(card),
                        "audit_json": asdict(verdict),
                    }
                )
            if result.recommendations:
                active_dates.add(day.signal_date)
            for card in result.recommendations:
                code = str(card.get("code") or "")
                price = float(
                    card.get("recommend_price")
                    or card.get("price")
                    or card.get("close")
                    or 0
                )
                if not code or price <= 0:
                    continue
                outcome = simulate(
                    RecommendationInput(
                        code=code,
                        signal_date=day.signal_date,
                        signal_price=price,
                    ),
                    day.outcome_bars.get(code, ()),
                    spec.execution_policy,
                )
                trades.append(outcome.trade)

        split_metrics = {}
        split_coverage = {}
        for name, dates in split.items():
            date_set = frozenset(dates)
            subset = tuple(trade for trade in trades if trade.signal_date in date_set)
            split_metrics[name] = compute_metrics(subset).canonical_payload()
            split_coverage[name] = _coverage(
                dates,
                active_dates=active_dates,
                opportunity_dates=opportunity_dates,
                executed_trades=sum(1 for trade in subset if trade.executed),
            ).to_dict()
        validation = cross_validate_trades(tuple(trades), spec.execution_policy).as_dict()
        public = {
            "policy_fingerprint": policy.fingerprint,
            "metrics": compute_metrics(tuple(trades)).canonical_payload(),
            "split_metrics": split_metrics,
            "coverage_evidence": _coverage(
                [day.signal_date for day in spec.candidate_snapshot.days],
                active_dates=active_dates,
                opportunity_dates=opportunity_dates,
                executed_trades=sum(1 for trade in trades if trade.executed),
            ).to_dict(),
            "split_coverage_evidence": split_coverage,
            "rule_verdict_summary": dict(sorted(verdict_summary.items())),
            "cross_validation": validation,
        }
        records = {"candidates": candidate_rows, "trades": trades}
        return public, records

    def _persist(
        self,
        spec: CanonicalPolicyExperimentSpec,
        artifact: Mapping[str, Any],
        *,
        champion_records: Mapping[str, Any],
        challenger_records: Mapping[str, Any],
    ) -> None:
        """Persist formal evidence only in the isolated ``backtest_*`` tables."""
        repository = self._repository
        snapshot = spec.candidate_snapshot
        snapshot_id = f"bps_{snapshot.fingerprint[:64]}"
        repository.persist_snapshot(
            snapshot_id=snapshot_id,
            snapshot_version=snapshot.schema_version,
            partition_manifest={
                "candidate_snapshot_fingerprint": snapshot.fingerprint,
                "candidate_source": snapshot.source,
                "signal_dates": [day.signal_date for day in snapshot.days],
            },
            coverage={
                "signal_date_count": len(snapshot.days),
                "candidate_count": sum(len(day.cards) for day in snapshot.days),
            },
            missing_requirements=[],
        )
        artifact_ids: dict[str, str] = {}
        for day in snapshot.days:
            payload = day.canonical_payload()
            artifact_id = f"bpa_{canonical_fingerprint(payload)[:64]}"
            repository.persist_artifact(
                artifact_id=artifact_id,
                snapshot_id=snapshot_id,
                signal_date=day.signal_date,
                artifact_scope="candidate",
                artifact_type="scored_candidate_day",
                schema_version=snapshot.schema_version,
                row_count=len(day.cards),
                content_encoding="canonical-json",
                payload=payload,
            )
            artifact_ids[day.signal_date] = artifact_id

        execution_fingerprint = spec.execution_policy.fingerprint()
        arm_rows: dict[str, str] = {}
        for name, policy, public, records in (
            ("champion", spec.champion_policy, artifact["arms"]["champion"], champion_records),
            ("challenger", spec.challenger_policy, artifact["arms"]["challenger"], challenger_records),
        ):
            request = {
                "schema_version": POLICY_EVIDENCE_SCHEMA_VERSION,
                "experiment_kind": "production_policy",
                "candidate_snapshot_fingerprint": snapshot.fingerprint,
                "policy": policy.canonical_payload(),
                "score_line": spec.score_line,
                "output_count": spec.output_count,
                "execution_policy": spec.execution_policy.canonical_payload(),
            }
            request_fingerprint = canonical_fingerprint(request)
            requested_id = f"btrp_{request_fingerprint[:64]}"
            claim = repository.claim_request(
                request_fingerprint=request_fingerprint,
                backtest_id=requested_id,
                snapshot_id=snapshot_id,
                strategy_id=f"production-policy:{policy.policy_id}",
                strategy_version=str(policy.version),
                request_schema_version=POLICY_EVIDENCE_SCHEMA_VERSION,
                request_json=request,
                execution_version="production-policy-runner:v2",
                execution_policy_fingerprint=execution_fingerprint,
                date_from=snapshot.days[0].signal_date,
                date_to=snapshot.days[-1].signal_date,
                experiment_id=spec.experiment_id,
            )
            arm_rows[name] = claim.backtest_id
            if claim.status != "completed":
                repository.mark_running(claim.backtest_id)
                candidates = []
                for row in records["candidates"]:
                    candidates.append(
                        {
                            **row,
                            "input_artifact_id": artifact_ids[row["signal_date"]],
                        }
                    )
                repository.persist_candidates(claim.backtest_id, candidates)
                repository.persist_trades(
                    claim.backtest_id,
                    [
                        {
                            "trade_id": (
                                "btt_"
                                + canonical_fingerprint(
                                    {
                                        "backtest_id": claim.backtest_id,
                                        "simulation_trade_id": trade.trade_id,
                                        "trade": trade.canonical_payload(),
                                    }
                                )[:48]
                            ),
                            "signal_date": trade.signal_date,
                            "code": trade.code,
                            "entry_date": trade.entry_date,
                            "exit_date": trade.exit_date,
                            "outcome_label": trade.outcome_label.value,
                            "trade_json": trade.canonical_payload(),
                        }
                        for trade in records["trades"]
                    ],
                )
                repository.complete_run(
                    claim.backtest_id,
                    result={
                        "status": "completed",
                        "experiment_kind": "production_policy",
                        "signal_dates": sorted(
                            day for values in artifact["split"].values() for day in values
                        ),
                        "candidate_snapshot_signal_dates": [
                            day.signal_date for day in snapshot.days
                        ],
                        **dict(public),
                    },
                )

        split = artifact["split"]
        experiment = repository.create_experiment(
            experiment_id=spec.experiment_id,
            hypothesis=spec.hypothesis,
            baseline_backtest_id=arm_rows["champion"],
            snapshot_id=snapshot_id,
            execution_policy_fingerprint=execution_fingerprint,
            split_json=split,
            split_hash=None,
            candidate_strategy_id=f"production-policy:{spec.challenger_policy.policy_id}",
            candidate_strategy_version=str(spec.challenger_policy.version),
            frozen_candidate_fingerprint=spec.challenger_policy.fingerprint,
            acceptance={
                "release_policy": RELEASE_POLICY_VERSION,
                "experiment_kind": "production_policy",
                "changed_rule_id": spec.changed_rule_id,
            },
        )
        repository.freeze_experiment(
            spec.experiment_id,
            frozen_candidate_fingerprint=spec.challenger_policy.fingerprint,
        )
        repository.complete_experiment(
            spec.experiment_id,
            status="completed",
            conclusion=dict(artifact),
            snapshot_id=str(experiment.snapshot_id),
            execution_policy_fingerprint=str(experiment.execution_policy_fingerprint),
            split_hash=str(experiment.split_hash),
            frozen_candidate_fingerprint=str(experiment.frozen_candidate_fingerprint),
        )
        session = getattr(repository, "session", None)
        if session is not None:
            session.commit()


def _changed_rule_ids(
    champion: ProductionSelectionPolicy,
    challenger: ProductionSelectionPolicy,
) -> tuple[str, ...]:
    left = champion.canonical_payload()
    right = challenger.canonical_payload()
    ignored = {"policy_id", "version"}
    aliases = {
        "excluded_20cm_buckets": "excluded_strategy_buckets",
    }
    return tuple(
        aliases.get(key, key)
        for key in sorted(left)
        if key not in ignored and left[key] != right[key]
    )


def _purged_split(dates: tuple[str, ...], *, purge_days: int) -> dict[str, list[str]]:
    if len(dates) < 6:
        raise ValueError("production-policy experiment requires at least six signal dates")
    train_end = max(1, int(len(dates) * 0.60))
    validation_end = max(train_end + 1, int(len(dates) * 0.80))
    validation_end = min(validation_end, len(dates) - 1)
    split = {
        "train": list(dates[:train_end]),
        "validation": list(dates[min(len(dates), train_end + purge_days):validation_end]),
        "out_of_sample": list(dates[min(len(dates), validation_end + purge_days):]),
    }
    if any(not values for values in split.values()):
        raise ValueError("purge/embargo left an empty experiment partition")
    return split


def _is_opportunity_day(cards: Sequence[Mapping[str, Any]], score_line: float) -> bool:
    from app.engine.aggregator import _execution_viability

    for raw in cards:
        card = dict(raw)
        if float(card.get("final_score") or 0) < score_line:
            continue
        if bool(card.get("hard_veto")):
            continue
        if float(card.get("recommend_price") or card.get("price") or card.get("close") or 0) <= 0:
            continue
        if _execution_viability(card)["status"] == "blocked":
            continue
        return True
    return False


def _coverage(
    dates: Sequence[str],
    *,
    active_dates: set[str],
    opportunity_dates: set[str],
    executed_trades: int,
) -> PortfolioCoverageEvidence:
    ordered = tuple(dates)
    opportunities = [day for day in ordered if day in opportunity_dates]
    active_opportunities = sum(1 for day in opportunities if day in active_dates)
    longest = current = 0
    for day in opportunities:
        if day in active_dates:
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return PortfolioCoverageEvidence(
        eligible_days=len(ordered),
        opportunity_days=len(opportunities),
        active_days=sum(1 for day in ordered if day in active_dates),
        executed_trades=executed_trades,
        opportunity_capture_rate_pct=round(
            active_opportunities * 100 / len(opportunities), 4
        ) if opportunities else 0.0,
        abstain_days=sum(1 for day in ordered if day not in active_dates),
        max_consecutive_opportunity_abstain_days=longest,
    )


__all__ = [
    "CanonicalPolicyExperimentSpec",
    "PortfolioCoverageEvidence",
    "ProductionPolicyBacktestRunner",
    "ScoredCandidateDay",
    "ScoredCandidateSnapshot",
]
