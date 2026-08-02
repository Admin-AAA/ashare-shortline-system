"""Read-only production rule replay that freezes pre-policy scored candidates."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Sequence

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.engine.backtest import BarInput, ExecutionPolicy, canonical_fingerprint
from app.engine.skill_loader import load_skills
from app.models.tables import DailyKbar, SkillRegistry
from app.services.backtest.policy_experiment import (
    ScoredCandidateDay,
    ScoredCandidateSnapshot,
)
from app.services.runs.stages.adapters import (
    ProductionFeatureBuild,
    ProductionMarketScope,
    ProductionMarketUniverse,
    ProductionRecommendationAssembly,
    ProductionStrategyEvaluation,
)
from app.services.workbench_service import _skill_version_id


_POLICY_MUTATION_KEYS = {
    "score_line_meta",
    "execution_viability",
    "policy_drop_owner",
    "policy_verdict_steps",
}

_POLICY_CARD_FIELDS = (
    "amount_yi",
    "bucket",
    "chg20",
    "close",
    "code",
    "data_authenticity",
    "data_completeness_score",
    "evidence_quality_score",
    "final_score",
    "hard_veto",
    "high",
    "industry",
    "is_limit_up",
    "limit_up_fd_amount_yi",
    "limit_up_first_time",
    "limit_up_last_time",
    "limit_up_open_times",
    "limit_up_seal_time",
    "limit_up_turnover_ratio",
    "low",
    "open",
    "pct",
    "pos60",
    "price",
    "recommend_price",
    "review_status",
    "support_ma5",
    "volume",
)

_WRITE_SQL_PREFIXES = (
    "ALTER ",
    "CREATE ",
    "DELETE ",
    "DROP ",
    "INSERT ",
    "MERGE ",
    "REPLACE ",
    "TRUNCATE ",
    "UPDATE ",
)


def _reject_write_statement(
    _connection: Any,
    _cursor: Any,
    statement: str,
    _parameters: Any,
    _context: Any,
    _executemany: bool,
) -> None:
    normalized = str(statement).lstrip().upper()
    if normalized.startswith(_WRITE_SQL_PREFIXES):
        raise RuntimeError("production rule replay attempted a database write")


def _pre_policy_card(card: dict[str, Any]) -> dict[str, Any]:
    full_pre_policy = {
        key: value
        for key, value in card.items()
        if key not in _POLICY_MUTATION_KEYS
        and not key.startswith("_regime_")
        and key != "_panic_day_penalty"
    }
    compact = {
        key: full_pre_policy[key]
        for key in _POLICY_CARD_FIELDS
        if key in full_pre_policy
    }
    raw_scores = full_pre_policy.get("raw_component_scores")
    if isinstance(raw_scores, dict) and "evidence" in raw_scores:
        compact["raw_component_scores"] = {"evidence": raw_scores["evidence"]}
    data_quality = full_pre_policy.get("data_quality")
    if isinstance(data_quality, dict) and "missing_fields" in data_quality:
        compact["data_quality"] = {"missing_fields": data_quality["missing_fields"]}
    signals = full_pre_policy.get("strategy_signals")
    if isinstance(signals, (list, tuple)):
        compact["strategy_signals"] = [
            {
                key: signal[key]
                for key in ("score", "runtime", "agent_review_unavailable")
                if key in signal
            }
            for signal in signals
            if isinstance(signal, dict)
        ]
    feature_snapshot = full_pre_policy.get("feature_snapshot")
    if isinstance(feature_snapshot, dict):
        for key in ("chg20", "pos60"):
            if key not in compact and key in feature_snapshot:
                compact[key] = feature_snapshot[key]
    compact["scored_card_fingerprint"] = canonical_fingerprint(full_pre_policy)
    return compact


class ProductionRuleReplayCandidateBuilder:
    """Build candidate snapshots with zero network calls and zero DB writes."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._market = ProductionMarketUniverse(db)
        self._scope = ProductionMarketScope(db)
        self._features = ProductionFeatureBuild(db)
        self._strategy = ProductionStrategyEvaluation(db)

    def build(
        self,
        *,
        start_date: str,
        end_date: str,
        execution_policy: ExecutionPolicy,
        score_line: float = 72.0,
        output_count: int = 6,
        skill_ids: Sequence[str] | None = None,
    ) -> ScoredCandidateSnapshot:
        from app.services.factors.registry import bootstrap_default_factor_registry

        if self._db.new or self._db.dirty or self._db.deleted:
            raise RuntimeError("production rule replay requires a clean database session")
        bootstrap_default_factor_registry()
        connection = self._db.connection()
        event.listen(connection, "before_cursor_execute", _reject_write_statement)
        try:
            return self._build_snapshot(
                start_date=start_date,
                end_date=end_date,
                execution_policy=execution_policy,
                score_line=score_line,
                output_count=output_count,
                skill_ids=skill_ids,
            )
        finally:
            event.remove(connection, "before_cursor_execute", _reject_write_statement)

    def _build_snapshot(
        self,
        *,
        start_date: str,
        end_date: str,
        execution_policy: ExecutionPolicy,
        score_line: float,
        output_count: int,
        skill_ids: Sequence[str] | None,
    ) -> ScoredCandidateSnapshot:
        selected_skills = self._selected_skills(skill_ids)
        if not selected_skills:
            raise ValueError("production rule replay has no enabled executable skills")
        trade_dates = tuple(
            str(value)
            for value in self._db.scalars(
                select(DailyKbar.trade_date)
                .where(DailyKbar.trade_date >= start_date, DailyKbar.trade_date <= end_date)
                .distinct()
                .order_by(DailyKbar.trade_date)
            ).all()
        )
        params = {
            "data_source": "local_pool",
            "market_scope": "all",
            "market_scope_value": None,
            "score_line": float(score_line),
            "output_count": int(output_count),
            "risk_template": "balanced",
            "hide_recent_repeats": False,
            "disable_agent_review": True,
            "skill_review_limit": 0,
        }
        days: list[ScoredCandidateDay] = []
        with self._db.no_autoflush:
            for signal_date in trade_dates:
                stocks, snapshot, _audit = self._market.load_universe(
                    data_source="local_pool",
                    as_of_date=signal_date,
                )
                snapshot = dict(snapshot)
                snapshot["trade_date"] = str(snapshot.get("trade_date") or signal_date)
                snapshot["strategy_versions"] = {
                    str(item["skill_id"]): str(item["version_id"])
                    for item in selected_skills
                }
                scoped = self._scope.apply_scope(stocks, params)
                prefiltered, prefilter_audit = self._scope.apply_prefilter(
                    scoped, selected_skills, params, snapshot
                )
                if not prefiltered:
                    days.append(
                        ScoredCandidateDay(
                            signal_date=signal_date,
                            market_snapshot=snapshot,
                            cards=(),
                            outcome_bars={},
                        )
                    )
                    continue
                _features, contexts = self._features.build(prefiltered, snapshot)
                _prelim, candidates, _gate, _pipeline = self._strategy.evaluate(
                    research_contexts=contexts,
                    selected_skills=selected_skills,
                    snapshot=snapshot,
                    params=params,
                    prefilter_audit=prefilter_audit,
                )
                candidate_results = tuple(
                    {
                        **dict(entry),
                        "review_skill_ids": [],
                        "agent_review_disabled": True,
                        "skill_reviews": [],
                    }
                    for entry in candidates
                )
                assembly = ProductionRecommendationAssembly(
                    self._db, f"backtest-rule-replay-{signal_date}"
                )
                _recommendations, scored_cards, _updates = assembly.aggregate(
                    candidate_results=candidate_results,
                    selected_skills=selected_skills,
                    snapshot=snapshot,
                    params=params,
                )
                cards = tuple(_pre_policy_card(dict(card)) for card in scored_cards)
                days.append(
                    ScoredCandidateDay(
                        signal_date=signal_date,
                        market_snapshot=snapshot,
                        cards=cards,
                        outcome_bars=self._outcome_bars(
                            signal_date,
                            [str(card.get("code") or "") for card in cards],
                            execution_policy,
                        ),
                    )
                )
        if self._db.new or self._db.dirty or self._db.deleted:
            raise RuntimeError("production rule replay attempted an ORM database write")
        return ScoredCandidateSnapshot(
            source="production-rule-replay",
            days=tuple(days),
        )

    def _selected_skills(self, requested: Sequence[str] | None) -> tuple[dict[str, Any], ...]:
        allowed = set(str(value) for value in requested) if requested else None
        registry_enabled = {
            str(skill_id): bool(enabled)
            for skill_id, enabled in self._db.execute(
                select(SkillRegistry.skill_id, SkillRegistry.enabled)
            ).all()
        }
        values = []
        for skill in load_skills(get_settings().skills_dir):
            if allowed is not None and skill.skill_id not in allowed:
                continue
            if not skill.enabled or not skill.executable or skill.contract is None:
                continue
            if registry_enabled and not registry_enabled.get(skill.skill_id, False):
                continue
            values.append(
                {
                    "skill_id": skill.skill_id,
                    "name": skill.name,
                    "weight": 1.0,
                    "version_id": _skill_version_id(skill),
                    "skill": skill,
                }
            )
        return tuple(sorted(values, key=lambda item: str(item["skill_id"])))

    def _outcome_bars(
        self,
        signal_date: str,
        codes: Sequence[str],
        execution_policy: ExecutionPolicy,
    ) -> dict[str, tuple[BarInput, ...]]:
        unique_codes = sorted({code for code in codes if code})
        if not unique_codes:
            return {}
        calendar_days = max(
            14,
            (execution_policy.entry_lookahead_days + execution_policy.horizon_days) * 3,
        )
        end = (date.fromisoformat(signal_date) + timedelta(days=calendar_days)).isoformat()
        rows = self._db.scalars(
            select(DailyKbar)
            .where(
                DailyKbar.code.in_(unique_codes),
                DailyKbar.trade_date > signal_date,
                DailyKbar.trade_date <= end,
            )
            .order_by(DailyKbar.code, DailyKbar.trade_date)
        ).all()
        limit = execution_policy.entry_lookahead_days + execution_policy.horizon_days + 2
        grouped: dict[str, list[BarInput]] = defaultdict(list)
        for row in rows:
            code = str(row.code)
            if len(grouped[code]) >= limit:
                continue
            prices = tuple(float(getattr(row, key) or 0) for key in ("open", "high", "low", "close"))
            if min(prices) <= 0:
                continue
            grouped[code].append(
                BarInput(
                    code=code,
                    trade_date=str(row.trade_date),
                    open=prices[0],
                    high=prices[1],
                    low=prices[2],
                    close=prices[3],
                    volume=float(row.volume) if row.volume is not None else None,
                    tradable=bool(float(row.volume or 0) > 0),
                )
            )
        return {code: tuple(values) for code, values in grouped.items()}


__all__ = ["ProductionRuleReplayCandidateBuilder"]
