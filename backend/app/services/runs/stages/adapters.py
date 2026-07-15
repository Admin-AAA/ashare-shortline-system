"""Production adapters that wire stage Ports to existing service implementations.

Each adapter is a thin wrapper around an existing function or service, providing
the Port protocol that the stage expects.  Adapters hold no business logic of
their own — they translate between Port signatures and service call signatures.
"""
from __future__ import annotations

import logging
from typing import Any, Sequence

from sqlalchemy.orm import Session

from .contracts import StageMapping
from .prepare import SkillResolutionPort, RunStartPort, ExecutionVersionPort
from .market_data import MarketUniversePort, MarketPersistencePort, MarketScopePort
from .auxiliary_data import AuxiliaryApplicationPort
from .features import FeatureBuildPort
from .strategy_evaluation import StrategyEvaluationPort
from .review import ReviewExecutorPort, CostRecordingPort
from .aggregation import RecommendationAssemblyPort
from .persistence import RecommendationPersistencePort

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prepare adapters
# ---------------------------------------------------------------------------

class ProductionSkillResolution(SkillResolutionPort):
    """Delegate skill resolution to existing workbench_service functions."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def sync_and_select(
        self,
        *,
        skill_ids: Sequence[dict[str, Any]],
    ) -> tuple[tuple[StageMapping, ...], dict[str, Any]]:
        from app.services.registry.skills import sync_skills
        from app.services.workbench_service import _select_skills_for_run

        skills = sync_skills(self._db)
        skill_map = {skill.skill_id: skill for skill in skills}
        selected = _select_skills_for_run(self._db, skill_map, list(skill_ids))
        return tuple(selected), skill_map


class ProductionRunStart(RunStartPort):
    """Delegate run start persistence to existing _persist_run_start."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def persist_run_start(
        self,
        run_id: str,
        params: StageMapping,
        selected_skills: Sequence[StageMapping],
        *,
        request: Any,
        execution_version: str | None,
    ) -> None:
        from app.services.workbench_service import _persist_run_start

        _persist_run_start(
            self._db,
            run_id,
            dict(params),
            list(selected_skills),
            request=request,
            execution_version=execution_version,
        )


class ProductionExecutionVersion(ExecutionVersionPort):
    """Delegate execution version computation to existing _execution_version_identity."""

    def compute(
        self,
        request: Any,
        selected_skills: Sequence[StageMapping],
    ) -> tuple[str, StageMapping]:
        from app.services.workbench_service import _execution_version_identity

        version, audit = _execution_version_identity(request, list(selected_skills))
        return version, audit


# ---------------------------------------------------------------------------
# Market data adapters
# ---------------------------------------------------------------------------

class ProductionMarketUniverse(MarketUniversePort):
    """Delegate market universe loading to load_legacy_market_universe."""

    def load_universe(
        self,
        *,
        data_source: str,
        as_of_date: str | None,
    ) -> tuple[tuple[StageMapping, ...], StageMapping, StageMapping]:
        from app.services.market_source.local_pool_adapter import load_legacy_market_universe

        stocks, snapshot, audit = load_legacy_market_universe(
            self._db,
            data_source=data_source,
            as_of_date=as_of_date,
        )
        return tuple(stocks), snapshot, audit

    def __init__(self, db: Session) -> None:
        self._db = db


class ProductionMarketPersistence(MarketPersistencePort):
    """Delegate market persistence to existing _persist_market / _persist_stocks_and_bars."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def persist_snapshot(self, snapshot: StageMapping) -> None:
        from app.services.workbench_service import _persist_market
        _persist_market(self._db, dict(snapshot))

    def persist_stocks_and_bars(
        self,
        stocks: Sequence[StageMapping],
        *,
        persist_bars: bool,
    ) -> None:
        from app.services.workbench_service import _persist_stocks_and_bars
        _persist_stocks_and_bars(self._db, list(stocks), persist_bars=persist_bars)


class ProductionMarketScope(MarketScopePort):
    """Delegate scope/prefilter to existing workbench helpers."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def apply_scope(
        self,
        stocks: Sequence[StageMapping],
        params: StageMapping,
    ) -> tuple[StageMapping, ...]:
        from app.services.workbench_service import _apply_market_scope_to_stocks
        scoped = _apply_market_scope_to_stocks(self._db, list(stocks), dict(params))
        return tuple(scoped)

    def apply_prefilter(
        self,
        scoped_stocks: Sequence[StageMapping],
        selected_skills: Sequence[StageMapping],
        params: StageMapping,
        snapshot: StageMapping,
    ) -> tuple[tuple[StageMapping, ...], StageMapping]:
        from app.services.auxiliary.application import (
            inject_auxiliary_prefilter_flags,
            load_auxiliary_feature_views,
        )
        from app.services.workbench_service import (
            apply_prefilters,
            resolve_prefilter_target_count,
        )

        prefilter_auxiliary = load_auxiliary_feature_views(
            self._db,
            [str(stock.get("code") or "") for stock in scoped_stocks if stock.get("code")],
            trade_date=str(snapshot.get("trade_date") or ""),
        )
        inject_auxiliary_prefilter_flags(list(scoped_stocks), prefilter_auxiliary)
        min_prefilter_count = resolve_prefilter_target_count(
            dict(params), selected_skill_count=len(selected_skills),
        )
        prefiltered_stocks, prefilter_audit = apply_prefilters(
            list(scoped_stocks),
            list(selected_skills),
            min_candidate_count=min_prefilter_count,
        )
        return tuple(prefiltered_stocks), prefilter_audit


# ---------------------------------------------------------------------------
# Auxiliary data adapter
# ---------------------------------------------------------------------------

class ProductionAuxiliaryApplication(AuxiliaryApplicationPort):
    """Delegate to the S4 AuxiliaryDataOrchestrator."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def run_application(
        self,
        *,
        instruments: Sequence[str],
        trade_date: str,
        refresh: bool,
    ) -> Any:
        from app.services.auxiliary.application import run_auxiliary_application

        return run_auxiliary_application(
            self._db,
            instruments=list(instruments),
            trade_date=trade_date,
            refresh=refresh,
        )


# ---------------------------------------------------------------------------
# Features adapter
# ---------------------------------------------------------------------------

class ProductionFeatureBuild(FeatureBuildPort):
    """Delegate feature building to build_features + build_research_contexts."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def build(
        self,
        stocks: Sequence[StageMapping],
        snapshot: StageMapping,
    ) -> tuple[tuple[StageMapping, ...], tuple[Any, ...]]:
        from app.services.feature_builder import build_features, build_research_contexts
        from app.services.workbench_service import _load_calibrated_dsa_weights

        features = build_features(
            list(stocks),
            dict(snapshot),
            _load_calibrated_dsa_weights(self._db),
            db=self._db,
        )
        contexts = build_research_contexts(features, dict(snapshot))
        return tuple(features), tuple(contexts)


# ---------------------------------------------------------------------------
# Strategy evaluation adapter
# ---------------------------------------------------------------------------

class ProductionStrategyEvaluation(StrategyEvaluationPort):
    """Delegate strategy evaluation to the inline workbench logic."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def evaluate(
        self,
        *,
        research_contexts: Sequence[Any],
        selected_skills: Sequence[StageMapping],
        snapshot: StageMapping,
        params: StageMapping,
    ) -> tuple[tuple[StageMapping, ...], tuple[StageMapping, ...], StageMapping, StageMapping]:
        from app.engine.strategy import build_registry_from_skills, StrategyContext
        from app.services.runs.skill_gate import build_skill_rule_gate, finalize_skill_gate_audit
        from app.services.runs.filter_audit import build_pipeline_audit
        from app.services.workbench_service import (
            _prelim_rule_score,
            _build_pipeline_audit,
            _strip_audit_rows,
            resolve_skill_review_limit,
        )
        from app.core.config import get_settings

        all_skill_objs = [s["skill"] for s in selected_skills]
        strategy_registry = build_registry_from_skills(all_skill_objs)
        settings = get_settings()

        # Rule scan
        prelim_entries: list[dict[str, Any]] = []
        for context in research_contexts:
            stock = context.to_skill_input()
            rule_signals_by_skill: dict[str, Any] = {}
            for selected in selected_skills:
                skill_obj = selected["skill"]
                prefilter_source = str(stock.get("prefilter_source") or "")
                prefilter_skill_ids = {str(sid) for sid in (stock.get("prefilter_skill_ids") or []) if sid}
                if prefilter_source == "skill_pass" and prefilter_skill_ids and skill_obj.skill_id not in prefilter_skill_ids:
                    signal = None
                else:
                    skip_universe = prefilter_source == "skill_pass" and skill_obj.skill_id in prefilter_skill_ids
                    ctx = StrategyContext(main_industries=snapshot.get("main_industries"), skip_universe_check=skip_universe)
                    signal = strategy_registry.get(skill_obj.skill_id).evaluate(stock, ctx) if skill_obj.executable else None
                rule_signals_by_skill[skill_obj.skill_id] = signal
            prelim_entries.append({
                "prelim_score": _prelim_rule_score(stock, rule_signals_by_skill, dict(snapshot)),
                "context": context,
                "rule_signals": rule_signals_by_skill,
                "stock": stock,
            })

        prelim_entries.sort(key=lambda item: float(item.get("prelim_score") or 0), reverse=True)
        gate_result = build_skill_rule_gate(prelim_entries, list(selected_skills), settings)
        skill_candidates = gate_result["skill_candidates"]
        non_skill_entries = gate_result["non_skill_entries"]

        skill_review_limit = resolve_skill_review_limit(
            dict(params), settings, selected_skill_count=len(selected_skills),
        )
        explicit_review_limit_skipped = 0
        if skill_review_limit > 0 and len(skill_candidates) > skill_review_limit:
            explicit_review_limit_skipped = len(skill_candidates) - skill_review_limit
            non_skill_entries = skill_candidates[skill_review_limit:] + non_skill_entries
            skill_candidates = skill_candidates[:skill_review_limit]

        skill_gate_audit = finalize_skill_gate_audit(
            gate_result["audit"],
            skill_candidates,
            explicit_review_limit_skipped=explicit_review_limit_skipped,
        )

        pipeline_audit = _build_pipeline_audit(
            snapshot=dict(snapshot),
            params=dict(params),
            total_contexts=len(research_contexts),
            prelim_count=len(prelim_entries),
            skill_input_count=len(skill_candidates),
            skill_agent_call_count=0,
            skill_gate_audit=skill_gate_audit,
            skill_review_limit=skill_review_limit,
            skill_review_concurrency=max(1, int(params.get("agent_review_concurrency") or 1)),
            skill_demo_limited=explicit_review_limit_skipped > 0,
            prefilter_audit={},
        )

        return tuple(prelim_entries), tuple(skill_candidates), skill_gate_audit, pipeline_audit


# ---------------------------------------------------------------------------
# Review adapter
# ---------------------------------------------------------------------------

class ProductionReviewExecutor(ReviewExecutorPort):
    """Delegate LLM review to run_skill_reviews."""

    async def review_candidates(
        self,
        candidates: Sequence[StageMapping],
        *,
        selected_skills: Sequence[StageMapping],
        snapshot: StageMapping,
        session_id: str,
        concurrency: int,
        agent_review_disabled: bool,
    ) -> tuple[StageMapping, ...]:
        import asyncio
        from app.agents.review_runner import run_skill_reviews

        if agent_review_disabled:
            return tuple(
                {**entry, "review_skill_ids": [], "agent_review_disabled": True, "skill_reviews": []}
                for entry in candidates
            )

        semaphore = asyncio.Semaphore(concurrency)

        async def _process(entry: dict[str, Any]) -> dict[str, Any]:
            stock = entry["stock"]
            review_skill_ids = set(entry.get("review_skill_ids") or [])
            review_skills = [
                selected["skill"]
                for selected in selected_skills
                if selected["skill_id"] in review_skill_ids
            ]
            async with semaphore:
                skill_reviews = await run_skill_reviews(
                    stock=stock,
                    skills=review_skills,
                    snapshot=dict(snapshot),
                    rule_signals_by_skill=entry["rule_signals"],
                    session_id=session_id,
                )
            return {**entry, "skill_reviews": skill_reviews}

        results = []
        tasks = [asyncio.create_task(_process(dict(entry))) for entry in candidates]
        for task in asyncio.as_completed(tasks):
            results.append(await task)
        return tuple(results)


class ProductionCostRecording(CostRecordingPort):
    """Delegate cost recording to drain_api_costs + record_api_call."""

    def __init__(self, db: Session, run_id: str) -> None:
        self._db = db
        self._run_id = run_id

    def record_costs(self, run_id: str) -> None:
        from app.agents.agno_skill_agent import drain_api_costs
        from app.services.cost_service import record_api_call

        api_costs = drain_api_costs()
        for cost in api_costs:
            record_api_call(self._db, run_id=run_id, **cost)


# ---------------------------------------------------------------------------
# Aggregation adapter
# ---------------------------------------------------------------------------

class ProductionRecommendationAssembly(RecommendationAssemblyPort):
    """Delegate card building and pool rules to existing helpers."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def aggregate(
        self,
        *,
        candidate_results: Sequence[StageMapping],
        selected_skills: Sequence[StageMapping],
        snapshot: StageMapping,
        params: StageMapping,
    ) -> tuple[tuple[StageMapping, ...], tuple[StageMapping, ...], StageMapping]:
        from app.services.workbench_service import (
            _build_recommendation_card,
            _recent_recommended_codes,
            _same_day_recommended_codes,
            _same_day_industry_counts,
            _load_calibrated_agg_weights,
            _drop_as_repeat_card,
        )
        from app.engine.aggregator import apply_pool_rules
        from app.services.feature_builder import market_regime_score

        skill_map = {s["skill_id"]: s["skill"] for s in selected_skills}
        recent_codes = _recent_recommended_codes(self._db, str(snapshot.get("trade_date") or ""))
        same_day_codes = _same_day_recommended_codes(self._db, str(snapshot.get("trade_date") or ""), None)
        same_day_industry_counts = _same_day_industry_counts(self._db, str(snapshot.get("trade_date") or ""), None)
        market_score = market_regime_score(dict(snapshot))
        calibrated_weights = _load_calibrated_agg_weights(self._db)

        cards: list[dict[str, Any]] = []
        for entry in candidate_results:
            skill_reviews = entry.get("skill_reviews") or []
            card = _build_recommendation_card(
                dict(entry),
                skill_reviews,
                skill_map=skill_map,
                selected_skills=list(selected_skills),
                market_score=market_score,
                risk_template=str(params.get("risk_template") or "balanced"),
                calibrated_weights=calibrated_weights,
                snapshot=dict(snapshot),
                recent_codes=recent_codes,
                same_day_codes=same_day_codes,
            )
            if _drop_as_repeat_card(
                card,
                dict(params),
                recent_repeat=bool(card.get("recent_repeat")),
                same_day_repeat=bool(card.get("same_day_repeat")),
            ):
                continue
            cards.append(card)

        scored_cards = list(cards)
        return tuple(cards), tuple(scored_cards), {}


# ---------------------------------------------------------------------------
# Persistence adapter
# ---------------------------------------------------------------------------

class ProductionRecommendationPersistence(RecommendationPersistencePort):
    """Delegate recommendation persistence to persist_recommendation_cards."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def persist(
        self,
        *,
        run_id: str,
        trade_date: str,
        cards: Sequence[StageMapping],
    ) -> tuple[Any, ...]:
        from app.services.runs.persistence import persist_recommendation_cards
        from app.services.workbench_service import _dumps

        saved = persist_recommendation_cards(
            self._db,
            run_id=run_id,
            trade_date=trade_date,
            cards=list(cards),
            dump_json=_dumps,
        )
        return tuple(saved)


__all__ = [
    "ProductionAuxiliaryApplication",
    "ProductionCostRecording",
    "ProductionExecutionVersion",
    "ProductionFeatureBuild",
    "ProductionMarketPersistence",
    "ProductionMarketScope",
    "ProductionMarketUniverse",
    "ProductionRecommendationAssembly",
    "ProductionRecommendationPersistence",
    "ProductionReviewExecutor",
    "ProductionRunStart",
    "ProductionSkillResolution",
    "ProductionStrategyEvaluation",
]
