#!/usr/bin/env python3
"""Capture the read-only pre-hardening production evidence baseline.

The artifact intentionally uses allowlisted projections. It never serializes
configuration objects, request payloads, arbitrary JSON columns, environment
variables, database URLs, or the full self-improvement state.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import inspect
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.api.dashboard import (
    _select_dashboard_recommendation_run,
    _select_dashboard_run,
)
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY
from app.engine.production_policy import DEFAULT_PRODUCTION_SELECTION_POLICY
from app.engine.skill_loader import load_skills
from app.engine.strategy import build_registry_from_skills
from app.main import app
from app.models.tables import (
    BacktestRunV2,
    BacktestTrade,
    Recommendation,
    RecommendationOutcome,
    RecommendationRun,
    SkillRegistry,
)
from app.services.official_sample_scope import official_sample_conditions
from app.services.runs.contracts import ExecutionPolicyIdentity
from app.services.workbench_service import list_recommendation_history


BASELINE_SCHEMA_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = (
    REPO_ROOT
    / ".kiro/specs/production-evidence-risk-hardening/baseline-task-0.1.json"
)
KEY_API_OPERATIONS: tuple[tuple[str, str], ...] = (
    ("/api/dashboard/overview", "get"),
    ("/api/recommendations/history", "get"),
    ("/api/recommendations/win-rate", "get"),
    ("/api/recommendations/outcome-summary", "get"),
    ("/api/runs", "get"),
    ("/api/runs/{run_id}", "get"),
)

_BACKTEST_TRADE_FIELDS = frozenset(
    {
        "code",
        "entry_date",
        "entry_price",
        "exit_date",
        "exit_price",
        "exit_reason",
        "gross_return_pct",
        "holding_days",
        "net_return_pct",
        "outcome_label",
        "position_size",
        "signal_date",
        "trade_id",
    }
)
_CHAMPION_METRIC_FIELDS = (
    "n",
    "trade_days",
    "win_rate_pct",
    "average_net_return_pct",
    "cumulative_net_return_pct",
    "profit_factor",
    "max_drawdown_pct",
    "caveat",
)
_CHAMPION_RULE_FIELDS = (
    "formal_board_universe",
    "excluded_20cm_buckets",
    "structural_strong_hard_veto",
    "oscillation_hard_veto",
    "position_sizing",
    "stop_loss_pct",
    "take_profit_pct",
    "trailing_profit_lock_ratio",
)
_HISTORY_SUMMARY_FIELDS = (
    "trade_count",
    "win_count",
    "win_rate",
    "average_net_return",
    "total_net_return",
    "additional_cost_pct",
)
_HISTORY_METHODOLOGY_FIELDS = (
    "sample_scope",
    "entry_policy",
    "win_definition",
    "deduplication",
    "return_unit",
)
_SCHEMA_KEYS = frozenset(
    {
        "$ref",
        "additionalProperties",
        "allOf",
        "anyOf",
        "default",
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "items",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "oneOf",
        "pattern",
        "properties",
        "required",
        "type",
    }
)
_FORBIDDEN_KEY_PARTS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "database_url",
        "dsn",
        "passwd",
        "password",
        "private_key",
        "secret",
    }
)
_FORBIDDEN_EXACT_KEYS = frozenset(
    {"access_token", "refresh_token", "token"}
)
_FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+\-/]+=*"),
    re.compile(r"(?i)\b(?:mysql|postgres(?:ql)?|mariadb)\+?[^:]*://[^\s/:@]+:[^\s@]+@"),
    re.compile(r"(?i)\b(?:password|passwd|api[_-]?key|access[_-]?token)\s*[=:]\s*\S+"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _digest(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _source_digest(*objects: Any) -> str:
    source = "\n".join(inspect.getsource(item) for item in objects)
    return sha256(source.encode("utf-8")).hexdigest()


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _date_part(value: Any) -> str | None:
    rendered = _iso(value)
    return rendered[:10] if rendered else None


def _json_object(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, Mapping):
        return dict(raw)
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def repository_identity() -> dict[str, Any]:
    status_lines = [line for line in _git("status", "--porcelain").splitlines() if line]
    return {
        "git_revision": _git("rev-parse", "HEAD"),
        "branch": _git("branch", "--show-current"),
        "worktree_dirty": bool(status_lines),
        "worktree_change_count": len(status_lines),
        "worktree_status_digest": _digest(status_lines),
    }


def group_evidence_rows(rows: Iterable[Sequence[Any]]) -> dict[str, Any]:
    """Group `(run_mode, scope, started_at, trade_date)` rows three ways."""
    scope_counts: Counter[tuple[str, str]] = Counter()
    started_counts: Counter[tuple[str, str, str | None]] = Counter()
    trade_counts: Counter[tuple[str, str, str | None]] = Counter()
    total = 0
    for run_mode, evaluation_scope, started_at, trade_date in rows:
        mode = str(run_mode or "__null__")
        scope = str(evaluation_scope or "__null__")
        started_date = _date_part(started_at)
        signal_date = _date_part(trade_date)
        scope_counts[(mode, scope)] += 1
        started_counts[(mode, scope, started_date)] += 1
        trade_counts[(mode, scope, signal_date)] += 1
        total += 1

    def scope_items() -> list[dict[str, Any]]:
        return [
            {"run_mode": mode, "evaluation_scope": scope, "count": count}
            for (mode, scope), count in sorted(scope_counts.items())
        ]

    def dated_items(
        values: Counter[tuple[str, str, str | None]], date_field: str
    ) -> list[dict[str, Any]]:
        return [
            {
                "run_mode": mode,
                "evaluation_scope": scope,
                date_field: grouped_date,
                "count": count,
            }
            for (mode, scope, grouped_date), count in sorted(
                values.items(), key=lambda item: (item[0][0], item[0][1], item[0][2] or "")
            )
        ]

    return {
        "total": total,
        "by_run_mode_and_evaluation_scope": scope_items(),
        "by_run_started_date": dated_items(started_counts, "started_date"),
        "by_trade_date": dated_items(trade_counts, "trade_date"),
        "invariants": {
            "scope_sum_matches_total": sum(scope_counts.values()) == total,
            "started_date_sum_matches_total": sum(started_counts.values()) == total,
            "trade_date_sum_matches_total": sum(trade_counts.values()) == total,
        },
    }


def collect_grouped_counts(db: Session) -> dict[str, Any]:
    run_rows = db.execute(
        select(
            RecommendationRun.run_mode,
            RecommendationRun.evaluation_scope,
            RecommendationRun.started_at,
            RecommendationRun.trade_date,
        )
    ).all()
    recommendation_rows = db.execute(
        select(
            RecommendationRun.run_mode,
            RecommendationRun.evaluation_scope,
            RecommendationRun.started_at,
            Recommendation.trade_date,
        ).join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
    ).all()
    outcome_rows = db.execute(
        select(
            RecommendationRun.run_mode,
            RecommendationRun.evaluation_scope,
            RecommendationRun.started_at,
            Recommendation.trade_date,
        )
        .select_from(RecommendationOutcome)
        .join(
            Recommendation,
            Recommendation.recommendation_id == RecommendationOutcome.recommendation_id,
        )
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
    ).all()
    return {
        "runs": group_evidence_rows(run_rows),
        "recommendations": group_evidence_rows(recommendation_rows),
        "outcomes": group_evidence_rows(outcome_rows),
    }


def _project_run(row: RecommendationRun | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "run_id": str(row.run_id),
        "run_mode": str(row.run_mode),
        "evaluation_scope": str(row.evaluation_scope),
        "trade_date": str(row.trade_date),
        "status": str(row.status),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "recommended_count": int(row.recommended_count or 0),
        "data_as_of": row.data_as_of,
        "source_snapshot_id": row.source_snapshot_id,
        "request_schema_version": row.request_schema_version,
        "execution_version": row.execution_version,
    }


def _current_execution_identity(row: RecommendationRun | None) -> dict[str, Any]:
    if row is None:
        return {"status": "unavailable"}
    summary = _json_object(row.result_summary_json)
    execution = summary.get("execution")
    execution = execution if isinstance(execution, dict) else {}
    policy = execution.get("execution_policy")
    policy = policy if isinstance(policy, dict) else {}
    factor_plan = execution.get("factor_plan")
    factor_plan = factor_plan if isinstance(factor_plan, dict) else {}
    application = execution.get("application")
    application = application if isinstance(application, dict) else {}
    strategy_fingerprints = execution.get("strategy_fingerprints")
    strategy_fingerprints = (
        strategy_fingerprints if isinstance(strategy_fingerprints, list) else []
    )
    projected_strategy_fingerprints = [
        {
            key: item.get(key)
            for key in (
                "strategy_id",
                "effective_version",
                "manifest_fingerprint",
                "implementation_fingerprint",
            )
        }
        for item in strategy_fingerprints
        if isinstance(item, dict)
    ]

    request = _json_object(row.request_json)
    selected_settings = {
        "data_source": request.get("data_source"),
        "disable_agent_review": request.get("disable_agent_review"),
        "risk_template": request.get("risk_template"),
        "skip_aux_refresh": request.get("skip_aux_refresh"),
    }
    derived_policy = ExecutionPolicyIdentity(
        policy_id="recommendation-run",
        policy_version="v1",
        settings=selected_settings,
    )
    observed_policy_fingerprint = policy.get("fingerprint")
    return {
        "status": "captured",
        "run_id": str(row.run_id),
        "execution_version": row.execution_version,
        "request_schema_version": row.request_schema_version,
        "application": {
            "version": application.get("version"),
            "revision": application.get("revision"),
        },
        "execution_policy": {
            "policy_id": policy.get("policy_id"),
            "policy_version": policy.get("policy_version"),
            "fingerprint": observed_policy_fingerprint,
            "selected_settings": selected_settings,
            "derived_fingerprint": derived_policy.fingerprint,
            "observed_matches_derived": observed_policy_fingerprint
            == derived_policy.fingerprint,
        },
        "factor_execution_plan": {
            "contract_version": factor_plan.get("contract_version"),
            "factor_ids": list(factor_plan.get("factor_ids") or []),
            "fingerprint": factor_plan.get("fingerprint"),
        },
        "strategy_fingerprints": projected_strategy_fingerprints,
        "strategy_fingerprints_digest": _digest(projected_strategy_fingerprints),
    }


def _fingerprint_counts(db: Session) -> dict[str, Any]:
    run_versions = db.execute(
        select(RecommendationRun.execution_version, func.count())
        .group_by(RecommendationRun.execution_version)
        .order_by(RecommendationRun.execution_version)
    ).all()
    backtest_policies = db.execute(
        select(BacktestRunV2.execution_policy_fingerprint, func.count())
        .group_by(BacktestRunV2.execution_policy_fingerprint)
        .order_by(BacktestRunV2.execution_policy_fingerprint)
    ).all()
    return {
        "recommendation_run_execution_versions": [
            {"execution_version": version, "count": int(count)}
            for version, count in run_versions
        ],
        "backtest_execution_policy_fingerprints": [
            {"execution_policy_fingerprint": fingerprint, "count": int(count)}
            for fingerprint, count in backtest_policies
        ],
    }


def collect_legacy_formal_execution_identity() -> dict[str, Any]:
    """Fingerprint the scattered pre-canonical formal execution semantics."""
    from app.engine import board_rules, trading_costs
    from app.services import entry_policy, execution_constraints, outcome_simulation

    horizon_default = RecommendationOutcome.__table__.c.horizon_days.default
    identity = {
        "policy_id": "legacy-formal-outcome",
        "schema_version": 1,
        "canonical_policy_object": False,
        "entry": {
            "policy": asdict(entry_policy.CAUSAL_STRATEGY_ENTRY),
            "lookahead_trade_days": outcome_simulation.ENTRY_LOOKAHEAD_DAYS,
            "intraday_signal_entry_supported": True,
        },
        "settlement": {
            "t_plus_one": True,
            "entry_bar_exit_allowed": False,
        },
        "horizon": {
            "source": "caller_supplied",
            "current_projection_default_days": int(horizon_default.arg),
        },
        "costs": {
            "commission_rate_pct": trading_costs.COMMISSION_RATE_PCT,
            "stamp_tax_rate_pct": trading_costs.STAMP_TAX_RATE_PCT,
            "additional_cost_pct": trading_costs.ADDITIONAL_COST_PCT,
            "stored_return_semantics": "slippage_included_fees_excluded",
            "success_semantics": "stored_return_strictly_exceeds_additional_cost",
        },
        "slippage": {
            "entry_bps": outcome_simulation.ENTRY_SLIPPAGE_BPS,
            "exit_bps": outcome_simulation.EXIT_SLIPPAGE_BPS,
        },
        "risk_rules": {
            "success_return_pct": outcome_simulation.SUCCESS_RETURN_PCT,
            "default_stop_loss_pct": outcome_simulation.DEFAULT_STOP_LOSS_PCT,
            "default_take_profit_pct": outcome_simulation.DEFAULT_TAKE_PROFIT_PCT,
            "max_stop_loss_pct": outcome_simulation.MAX_STOP_LOSS_PCT,
            "max_take_profit_pct": outcome_simulation.MAX_TAKE_PROFIT_PCT,
            "stop_loss_gap_cap_pct": outcome_simulation.STOP_LOSS_GAP_CAP_PCT,
            "max_entry_gap_pct": outcome_simulation.MAX_ENTRY_GAP_PCT,
            "trailing_stop_activate_pct": outcome_simulation.TRAILING_STOP_ACTIVATE_PCT,
            "trailing_stop_callback_pct": outcome_simulation.TRAILING_STOP_CALLBACK_PCT,
            "trailing_stop_min_lock_pct": outcome_simulation.TRAILING_STOP_MIN_LOCK_PCT,
            "trailing_stop_profit_lock_ratio": (
                outcome_simulation.TRAILING_STOP_PROFIT_LOCK_RATIO
            ),
        },
        "exit_semantics": {
            "resolver": "causal_profit_floor",
            "same_bar_both_hit": "stop_loss_first",
            "priority": ["hard_stop", "prior_day_trailing_stop", "take_profit"],
            "board_limit_rules": "app.engine.board_rules",
            "liquidity_rules": "app.services.execution_constraints",
        },
        "position_sizing": {
            "source": "feature_snapshot",
            "implementation": "app.services.outcome_simulation._resolve_position_size",
        },
        "implementation_source_sha256": _source_digest(
            outcome_simulation,
            entry_policy,
            trading_costs,
            board_rules,
            execution_constraints,
        ),
    }
    return {**identity, "fingerprint": _digest(identity)}


def collect_fingerprints(
    db: Session, current_run: RecommendationRun | None
) -> dict[str, Any]:
    policy = DEFAULT_PRODUCTION_SELECTION_POLICY
    return {
        "production_selection_policy": {
            "policy_id": policy.policy_id,
            "version": policy.version,
            "fingerprint": policy.fingerprint,
        },
        "default_research_execution_policy": {
            "schema_version": DEFAULT_EXECUTION_POLICY.schema_version,
            "fingerprint": DEFAULT_EXECUTION_POLICY.fingerprint(),
        },
        "legacy_formal_outcome_execution": collect_legacy_formal_execution_identity(),
        "dashboard_current_run_execution": _current_execution_identity(current_run),
        "persisted_identity_counts": _fingerprint_counts(db),
    }


def collect_strategy_inventory(db: Session) -> dict[str, Any]:
    skills = load_skills(get_settings().skills_dir)
    registry = build_registry_from_skills(skills)
    descriptors = sorted(registry.list_descriptors(), key=lambda item: item.strategy_id)
    active = [item for item in descriptors if item.available_for_execution]
    persisted_rows = db.execute(
        select(
            SkillRegistry.skill_id,
            SkillRegistry.current_version,
            SkillRegistry.enabled,
            SkillRegistry.has_executable_contract,
        ).order_by(SkillRegistry.skill_id)
    ).all()
    persisted_active_ids = sorted(
        str(skill_id)
        for skill_id, _version, enabled, executable in persisted_rows
        if enabled and executable
    )
    repository_active_ids = [item.strategy_id for item in active]
    return {
        "definition": "repository descriptor is active, executable, and not deprecated",
        "active_count": len(active),
        "active": [
            {
                "strategy_id": item.strategy_id,
                "name": item.name,
                "mode": item.mode.value,
                "declared_version": item.declared_version,
                "effective_version": item.effective_version,
                "manifest_fingerprint": item.manifest_hash,
                "implementation_fingerprint": item.implementation_hash,
                "status": item.status.value,
            }
            for item in active
        ],
        "repository_non_active": [
            {"strategy_id": item.strategy_id, "status": item.status.value}
            for item in descriptors
            if not item.available_for_execution
        ],
        "persisted_registry_active_ids": persisted_active_ids,
        "inventory_consistency": {
            "repository_only": sorted(set(repository_active_ids) - set(persisted_active_ids)),
            "persisted_only": sorted(set(persisted_active_ids) - set(repository_active_ids)),
            "matches": repository_active_ids == persisted_active_ids,
        },
    }


def _load_champion_state() -> dict[str, Any]:
    configured = os.environ.get("SELF_IMPROVE_MEMORY_DIR")
    default_memory_dir = (
        Path.home()
        / ".claude/projects"
        / f"-Users-{Path.home().name}"
        / "memory"
    )
    memory_dir = Path(configured) if configured else default_memory_dir
    path = memory_dir / "self-improve-state.json"
    if not path.exists():
        return {}
    return _json_object(path.read_text(encoding="utf-8"))


def project_champion_state(
    state: Mapping[str, Any], *, git_revision: str
) -> dict[str, Any]:
    champion = state.get("canonical_production")
    champion = champion if isinstance(champion, Mapping) else {}
    metrics = champion.get("metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    raw_rules = champion.get("rules")
    rules = raw_rules if isinstance(raw_rules, Mapping) else {}
    raw_selection_policy = champion.get("selection_policy")
    selection_policy = (
        raw_selection_policy if isinstance(raw_selection_policy, Mapping) else {}
    )
    governance = state.get("selection_policy_governance")
    governance = governance if isinstance(governance, Mapping) else {}

    declared_commit = champion.get("commit")
    commit_matches = bool(declared_commit) and git_revision.startswith(str(declared_commit))
    policy = DEFAULT_PRODUCTION_SELECTION_POLICY
    comparisons = {
        "formal_board_universe": policy.board_universe,
        "excluded_20cm_buckets": list(policy.excluded_20cm_buckets),
        "structural_strong_hard_veto": policy.structural_strong_hard_veto,
        "oscillation_hard_veto": policy.oscillation_hard_veto,
    }
    mismatches = [
        field
        for field, current_value in comparisons.items()
        if field in rules and rules.get(field) != current_value
    ]
    governance_fingerprint = governance.get("policy_artifact_fingerprint")
    declared_policy_fingerprint = (
        selection_policy.get("fingerprint") or governance_fingerprint
    )
    fingerprint_matches = declared_policy_fingerprint == policy.fingerprint
    policy_id_matches = (
        selection_policy.get("policy_id") == policy.policy_id
        if selection_policy
        else None
    )
    policy_version_matches = (
        selection_policy.get("version") == policy.version
        if selection_policy
        else None
    )
    governance_fingerprint_matches = (
        governance_fingerprint == policy.fingerprint
        if governance_fingerprint is not None
        else None
    )
    policy_identity_matches = (
        fingerprint_matches
        and policy_id_matches is not False
        and policy_version_matches is not False
        and governance_fingerprint_matches is not False
    )
    has_champion = bool(champion)
    stale = has_champion and (
        not commit_matches or bool(mismatches) or not policy_identity_matches
    )
    if not has_champion:
        interpretation = "no champion declaration was found in shared self-improve state"
    elif stale:
        interpretation = (
            "legacy metrics are audit-only and do not establish current-policy expectancy"
        )
    else:
        interpretation = "legacy declaration matches the identities checked by this baseline"
    return {
        "source": "shared_self_improve_state_allowlist_projection",
        "source_schema_version": state.get("schema_version"),
        "last_checked_at": state.get("last_checked_at"),
        "legacy_canonical_production_present": has_champion,
        "current_champion_field_present": isinstance(state.get("current_champion"), Mapping),
        "identity_status": (
            "stale_identity" if stale else "declared_current" if has_champion else "missing"
        ),
        "declared": {
            "branch": champion.get("branch"),
            "commit": declared_commit,
            "provisional": champion.get("provisional"),
            "provisional_reason": champion.get("provisional_reason"),
            "rollback_point": champion.get("rollback_point"),
            "metrics": {key: metrics.get(key) for key in _CHAMPION_METRIC_FIELDS},
            "legacy_rules_present": isinstance(raw_rules, Mapping),
            "rules": {
                key: rules.get(key) for key in _CHAMPION_RULE_FIELDS if key in rules
            },
            "selection_policy": {
                key: selection_policy.get(key)
                for key in ("policy_id", "version", "fingerprint")
                if key in selection_policy
            },
            "policy_fingerprint": declared_policy_fingerprint,
            "governance_policy_fingerprint": governance_fingerprint,
        },
        "identity_checks": {
            "declared_commit_matches_git_revision": commit_matches,
            "declared_policy_fingerprint_matches_current": fingerprint_matches,
            "declared_policy_id_matches_current": policy_id_matches,
            "declared_policy_version_matches_current": policy_version_matches,
            "governance_policy_fingerprint_matches_current": (
                governance_fingerprint_matches
            ),
            "mismatched_rule_fields": mismatches,
            "current_policy_fingerprint": policy.fingerprint,
        },
        "interpretation": interpretation,
    }


def collect_official_projection(db: Session) -> dict[str, Any]:
    conditions = official_sample_conditions()
    stats = db.execute(
        select(
            func.count(Recommendation.recommendation_id),
            func.count(RecommendationOutcome.recommendation_id),
            func.count(func.distinct(Recommendation.run_id)),
            func.count(func.distinct(Recommendation.trade_date)),
            func.max(Recommendation.trade_date),
        )
        .select_from(Recommendation)
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .outerjoin(
            RecommendationOutcome,
            RecommendationOutcome.recommendation_id == Recommendation.recommendation_id,
        )
        .where(*conditions)
    ).one()
    sample_rows = db.execute(
        select(Recommendation, RecommendationRun, RecommendationOutcome)
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .outerjoin(
            RecommendationOutcome,
            RecommendationOutcome.recommendation_id == Recommendation.recommendation_id,
        )
        .where(*conditions)
        .order_by(Recommendation.trade_date.desc(), Recommendation.final_score.desc())
        .limit(5)
    ).all()
    return {
        "implementation": "app.services.official_sample_scope.official_sample_conditions",
        "implementation_source_sha256": _source_digest(official_sample_conditions),
        "eligibility_rules": [
            "run_mode == formal",
            "evaluation_scope == live_forward",
            "run_status == completed",
            "data_as_of is present and <= recommendation.trade_date",
            "source_snapshot_id is present",
            "recommendation_id starts with rec_",
            "Beijing date(recommended_at) == recommendation.trade_date",
        ],
        "counts": {
            "recommendations": int(stats[0] or 0),
            "outcomes": int(stats[1] or 0),
            "distinct_runs": int(stats[2] or 0),
            "distinct_trade_dates": int(stats[3] or 0),
            "latest_trade_date": stats[4],
        },
        "latest_sample": [
            {
                "recommendation_id": recommendation.recommendation_id,
                "run_id": recommendation.run_id,
                "code": recommendation.code,
                "trade_date": recommendation.trade_date,
                "recommended_at": _iso(recommendation.recommended_at),
                "final_score": recommendation.final_score,
                "run_mode": run.run_mode,
                "evaluation_scope": run.evaluation_scope,
                "outcome_label": outcome.outcome_label if outcome else None,
                "entry_date": outcome.entry_date if outcome else None,
                "exit_date": outcome.exit_date if outcome else None,
                "exit_reason": outcome.exit_reason if outcome else None,
                "executed_return": outcome.executed_return if outcome else None,
            }
            for recommendation, run, outcome in sample_rows
        ],
    }


def collect_history_projection(db: Session) -> dict[str, Any]:
    projection = list_recommendation_history(db, limit=1, offset=0)
    summary = projection.get("summary")
    summary = summary if isinstance(summary, Mapping) else {}
    methodology = summary.get("methodology")
    methodology = methodology if isinstance(methodology, Mapping) else {}
    items = projection.get("items")
    items = items if isinstance(items, list) else []
    first_item = items[0] if items and isinstance(items[0], Mapping) else {}
    first_outcome = first_item.get("outcome")
    first_outcome = first_outcome if isinstance(first_outcome, Mapping) else {}
    return {
        "implementation": "app.services.workbench_service.list_recommendation_history",
        "implementation_source_sha256": _source_digest(list_recommendation_history),
        "default_filters": {"limit": 50, "offset": 0},
        "selection_contract": {
            "base_projection": "official_sample_conditions",
            "deduplication": "trade_date_and_code_keep_highest_final_score",
            "ordering": ["trade_date_desc", "final_score_desc"],
        },
        "runtime_projection": {
            "total": int(projection.get("total") or 0),
            "summary": {key: summary.get(key) for key in _HISTORY_SUMMARY_FIELDS},
            "methodology": {
                key: methodology.get(key) for key in _HISTORY_METHODOLOGY_FIELDS
            },
            "response_keys": sorted(str(key) for key in projection),
            "item_keys": sorted(str(key) for key in first_item),
            "outcome_keys": sorted(str(key) for key in first_outcome),
        },
    }


def collect_dashboard_projection(db: Session) -> tuple[dict[str, Any], RecommendationRun | None]:
    current_run = _select_dashboard_run(db)
    recommendation_run = _select_dashboard_recommendation_run(db)
    return (
        {
            "implementation": "app.api.dashboard._select_dashboard_run",
            "implementation_source_sha256": _source_digest(
                _select_dashboard_run, _select_dashboard_recommendation_run
            ),
            "current_run_selection": [
                "evaluation_scope live_forward first",
                "trade_date descending",
                "completed before active before other status",
                "started_at descending",
            ],
            "recommendation_run_selection": [
                "latest Dashboard trade date",
                "completed formal with recommendations first",
                "completed formal even when empty second",
                "any completed run for the date as fallback",
            ],
            "current_run": _project_run(current_run),
            "recommendation_run": _project_run(recommendation_run),
        },
        current_run,
    )


def _schema_shape(value: Any) -> Any:
    if isinstance(value, list):
        return [_schema_shape(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key not in _SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(item, Mapping):
            result[key] = {
                str(name): _schema_shape(schema) for name, schema in sorted(item.items())
            }
        else:
            result[key] = _schema_shape(item)
    return result


def _find_schema_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, Mapping):
        ref = value.get("$ref")
        prefix = "#/components/schemas/"
        if isinstance(ref, str) and ref.startswith(prefix):
            refs.add(ref[len(prefix) :])
        for item in value.values():
            refs.update(_find_schema_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(_find_schema_refs(item))
    return refs


def collect_api_schemas() -> dict[str, Any]:
    document = app.openapi()
    operations: list[dict[str, Any]] = []
    for path, method in KEY_API_OPERATIONS:
        operation = document["paths"][path][method]
        request_schema = (
            operation.get("requestBody", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema")
        )
        responses: dict[str, Any] = {}
        for status, response in sorted(operation.get("responses", {}).items()):
            if not str(status).startswith("2"):
                continue
            schema = (
                response.get("content", {})
                .get("application/json", {})
                .get("schema")
            )
            responses[str(status)] = _schema_shape(schema or {})
        operations.append(
            {
                "path": path,
                "method": method.upper(),
                "operation_id": operation.get("operationId"),
                "parameters": [
                    {
                        "name": parameter.get("name"),
                        "in": parameter.get("in"),
                        "required": bool(parameter.get("required")),
                        "schema": _schema_shape(parameter.get("schema") or {}),
                    }
                    for parameter in operation.get("parameters", [])
                ],
                "request_schema": _schema_shape(request_schema or {}),
                "success_responses": responses,
            }
        )

    schemas = document.get("components", {}).get("schemas", {})
    referenced = _find_schema_refs(operations)
    components: dict[str, Any] = {}
    pending = sorted(referenced)
    while pending:
        name = pending.pop(0)
        if name in components or name not in schemas:
            continue
        shaped = _schema_shape(schemas[name])
        components[name] = shaped
        pending.extend(sorted(_find_schema_refs(shaped) - set(components)))
    contract = {"operations": operations, "components": components}
    return {**contract, "contract_sha256": _digest(contract)}


def project_backtest_trade_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: payload.get(key)
        for key in sorted(_BACKTEST_TRADE_FIELDS)
        if key in payload
    }


def _project_backtest_trade(
    trade: BacktestTrade, run: BacktestRunV2
) -> dict[str, Any]:
    payload = project_backtest_trade_payload(_json_object(trade.trade_json))
    payload.update(
        {
            "trade_id": trade.trade_id,
            "backtest_id": trade.backtest_id,
            "strategy_id": run.strategy_id,
            "strategy_version": run.strategy_version,
            "execution_version": run.execution_version,
            "execution_policy_fingerprint": run.execution_policy_fingerprint,
            "signal_date": trade.signal_date,
            "code": trade.code,
            "entry_date": trade.entry_date,
            "exit_date": trade.exit_date,
            "outcome_label": trade.outcome_label,
        }
    )
    return payload


def _project_formal_trade(
    recommendation: Recommendation,
    outcome: RecommendationOutcome,
    run: RecommendationRun,
) -> dict[str, Any]:
    return {
        "recommendation_id": recommendation.recommendation_id,
        "run_id": recommendation.run_id,
        "run_mode": run.run_mode,
        "evaluation_scope": run.evaluation_scope,
        "execution_version": run.execution_version,
        "trade_date": recommendation.trade_date,
        "code": recommendation.code,
        "final_score": recommendation.final_score,
        "entry_policy_id": outcome.entry_policy_id,
        "horizon_days": outcome.horizon_days,
        "entry_triggered": bool(outcome.entry_triggered),
        "entry_date": outcome.entry_date,
        "entry_price": outcome.entry_price,
        "exit_date": outcome.exit_date,
        "exit_price": outcome.exit_price,
        "exit_reason": outcome.exit_reason,
        "executed_return": outcome.executed_return,
        "weighted_return": outcome.weighted_return,
        "position_size": outcome.position_size,
        "holding_days": outcome.holding_days,
        "entry_slippage_bps": outcome.entry_slippage_bps,
        "exit_slippage_bps": outcome.exit_slippage_bps,
        "outcome_label": outcome.outcome_label,
        "label_source": outcome.label_source,
    }


def _representatives_by_reason(
    values: Iterable[dict[str, Any]], *, limit: int = 12
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in values:
        key = (
            str(item.get("exit_reason") or "no_exit_reason"),
            str(item.get("outcome_label") or "unknown"),
        )
        if key in seen:
            continue
        seen.add(key)
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def collect_golden_trades(db: Session) -> dict[str, Any]:
    formal_rows = db.execute(
        select(Recommendation, RecommendationOutcome, RecommendationRun)
        .join(
            RecommendationOutcome,
            RecommendationOutcome.recommendation_id == Recommendation.recommendation_id,
        )
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .where(*official_sample_conditions())
        .order_by(
            Recommendation.trade_date.desc(),
            Recommendation.recommendation_id.asc(),
        )
    ).all()
    formal_values = [
        _project_formal_trade(recommendation, outcome, run)
        for recommendation, outcome, run in formal_rows
    ]
    formal_same_day = [
        item
        for item in formal_values
        if item.get("entry_date") is not None
        and item.get("entry_date") == item.get("exit_date")
    ]

    backtest_rows = db.execute(
        select(BacktestTrade, BacktestRunV2)
        .join(BacktestRunV2, BacktestRunV2.backtest_id == BacktestTrade.backtest_id)
        .where(BacktestRunV2.status == "completed")
        .order_by(
            BacktestRunV2.finished_at.desc(),
            BacktestRunV2.created_at.desc(),
            BacktestTrade.trade_id.asc(),
        )
        .limit(5000)
    ).all()
    backtest_values = [
        _project_backtest_trade(trade, run) for trade, run in backtest_rows
    ]
    backtest_total = int(
        db.scalar(
            select(func.count())
            .select_from(BacktestTrade)
            .join(BacktestRunV2, BacktestRunV2.backtest_id == BacktestTrade.backtest_id)
            .where(BacktestRunV2.status == "completed")
        )
        or 0
    )
    backtest_same_day_total = int(
        db.scalar(
            select(func.count())
            .select_from(BacktestTrade)
            .join(BacktestRunV2, BacktestRunV2.backtest_id == BacktestTrade.backtest_id)
            .where(
                BacktestRunV2.status == "completed",
                BacktestTrade.entry_date.is_not(None),
                BacktestTrade.entry_date == BacktestTrade.exit_date,
            )
        )
        or 0
    )
    backtest_same_day_rows = db.execute(
        select(BacktestTrade, BacktestRunV2)
        .join(BacktestRunV2, BacktestRunV2.backtest_id == BacktestTrade.backtest_id)
        .where(
            BacktestRunV2.status == "completed",
            BacktestTrade.entry_date.is_not(None),
            BacktestTrade.entry_date == BacktestTrade.exit_date,
        )
        .order_by(
            BacktestRunV2.finished_at.desc(),
            BacktestRunV2.created_at.desc(),
            BacktestTrade.trade_id.asc(),
        )
        .limit(12)
    ).all()
    return {
        "selection": {
            "representative_rule": "latest deterministic first trade per exit_reason and outcome_label",
            "same_day_rule": "entry_date is present and equals exit_date",
            "sample_limit": 12,
        },
        "formal_official": {
            "population_count": len(formal_values),
            "representative_trades": _representatives_by_reason(formal_values),
            "same_day_exit": {
                "count": len(formal_same_day),
                "ratio": (
                    round(len(formal_same_day) / len(formal_values), 8)
                    if formal_values
                    else 0.0
                ),
                "samples": formal_same_day[:12],
            },
        },
        "research_backtest": {
            "population_count": backtest_total,
            "representative_scan_count": len(backtest_values),
            "representative_trades": _representatives_by_reason(backtest_values),
            "same_day_exit": {
                "count": backtest_same_day_total,
                "ratio": (
                    round(backtest_same_day_total / backtest_total, 8)
                    if backtest_total
                    else 0.0
                ),
                "samples": [
                    _project_backtest_trade(trade, run)
                    for trade, run in backtest_same_day_rows
                ],
            },
        },
    }


def _forbidden_literals_from_settings() -> tuple[str, ...]:
    settings = get_settings()
    values: set[str] = set()
    try:
        url = make_url(settings.database_url)
    except Exception:
        url = None
    if url is not None:
        for value in (url.username, url.password):
            if value and len(str(value)) >= 6:
                values.add(str(value))
    for field_name, value in vars(settings).items():
        normalized = str(field_name).lower()
        if not (
            normalized in _FORBIDDEN_EXACT_KEYS
            or any(part in normalized for part in _FORBIDDEN_KEY_PARTS)
        ):
            continue
        if isinstance(value, str) and len(value) >= 6:
            values.add(value)
    return tuple(sorted(values))


def assert_safe_output(
    value: Any, *, forbidden_literals: Iterable[str] = ()
) -> None:
    """Fail closed before an artifact containing credential-like data is written."""
    def visit(item: Any, path: str) -> None:
        if isinstance(item, Mapping):
            for raw_key, nested in item.items():
                key = str(raw_key).lower()
                if key in _FORBIDDEN_EXACT_KEYS or any(
                    part in key for part in _FORBIDDEN_KEY_PARTS
                ):
                    raise ValueError(f"unsafe output key at {path}.{raw_key}")
                visit(nested, f"{path}.{raw_key}")
            return
        if isinstance(item, (list, tuple)):
            for index, nested in enumerate(item):
                visit(nested, f"{path}[{index}]")
            return
        if not isinstance(item, str):
            return
        for pattern in _FORBIDDEN_VALUE_PATTERNS:
            if pattern.search(item):
                raise ValueError(f"unsafe output value at {path}")

    visit(value, "$")
    rendered = _canonical_json(value)
    for literal in forbidden_literals:
        if literal and literal in rendered:
            raise ValueError("configured sensitive literal found in output")


def collect_baseline(db: Session) -> dict[str, Any]:
    repository = repository_identity()
    dashboard_projection, current_run = collect_dashboard_projection(db)
    payload = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "task": "0.1 固化整改前事实基线",
        "spec": "production-evidence-risk-hardening",
        "requirements": ["9.3", "10.1", "10.8", "10.10"],
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "capture_semantics": {
            "database_access": "single read-only transaction rolled back on exit",
            "payload_policy": "allowlisted projections only",
            "full_json_columns_emitted": False,
            "safety_check": "fail closed before write",
        },
        "repository": repository,
        "fingerprints": collect_fingerprints(db, current_run),
        "active_strategy_inventory": collect_strategy_inventory(db),
        "champion_state": project_champion_state(
            _load_champion_state(), git_revision=repository["git_revision"]
        ),
        "grouped_counts": collect_grouped_counts(db),
        "projections": {
            "official": collect_official_projection(db),
            "historical_page": collect_history_projection(db),
            "dashboard": dashboard_projection,
        },
        "key_api_schemas": collect_api_schemas(),
        "golden_trades": collect_golden_trades(db),
        "known_unknowns": [
            {
                "topic": "current_policy_true_forward_expectancy",
                "status": "unknown",
                "reason": "legacy champion identity does not validate the current git and policy identity",
            },
            {
                "topic": "auxiliary_data_missingness_selection_impact",
                "status": "unknown",
                "reason": "no paired real-versus-degraded counterfactual is established by this baseline",
            },
        ],
    }
    payload["section_digests"] = {
        key: _digest(payload[key])
        for key in (
            "repository",
            "fingerprints",
            "active_strategy_inventory",
            "champion_state",
            "grouped_counts",
            "projections",
            "key_api_schemas",
            "golden_trades",
        )
    }
    return payload


def _start_read_only_transaction(db: Session) -> str:
    dialect = db.get_bind().dialect.name
    if dialect == "mysql":
        db.execute(text("SET TRANSACTION READ ONLY"))
    return dialect


def write_baseline(output: Path) -> dict[str, Any]:
    db = SessionLocal()
    try:
        dialect = _start_read_only_transaction(db)
        payload = collect_baseline(db)
        payload["capture_semantics"]["database_dialect"] = dialect
        assert_safe_output(
            payload,
            forbidden_literals=_forbidden_literals_from_settings(),
        )
        db.rollback()
    finally:
        db.close()

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f"{output.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, output)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture a read-only, allowlisted production evidence baseline."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = write_baseline(args.output.resolve())
    print(
        json.dumps(
            {
                "output": str(args.output),
                "schema_version": payload["schema_version"],
                "runs": payload["grouped_counts"]["runs"]["total"],
                "recommendations": payload["grouped_counts"]["recommendations"]["total"],
                "outcomes": payload["grouped_counts"]["outcomes"]["total"],
                "safety_check": "passed",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
