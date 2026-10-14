"""Production adapters that wire stage Ports to existing service implementations.

Each adapter is a thin wrapper around an existing function or service, providing
the Port protocol that the stage expects.  Adapters hold no business logic of
their own — they translate between Port signatures and service call signatures.
"""
from __future__ import annotations

import logging
from typing import Any, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from .contracts import StageMapping
from .prepare import SkillResolutionPort, RunStartPort, ExecutionVersionPort
from .market_data import MarketUniversePort, MarketPersistencePort, MarketScopePort
from .auxiliary_data import AuxiliaryApplicationPort
from .features import FeatureBuildPort
from .strategy_evaluation import StrategyEvaluationPort
from .review import ReviewExecutorPort, CostRecordingPort
from .aggregation import RecommendationAssemblyPort
from .persistence import RecommendationPersistencePort, SealedRecommendationCollection

logger = logging.getLogger(__name__)

_SHARED_MARKET_WRITE_LOCK = "ashare_shared_market_write"
# 全市场日K一次写入实测约 1~2 分钟；排在后面的 run 最多再等两三个前序写入。
_SHARED_MARKET_WRITE_LOCK_TIMEOUT_SECONDS = 600


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
        run_mode: str | None = None,
        preset_id: str | None = None,
    ) -> tuple[tuple[StageMapping, ...], dict[str, Any]]:
        from app.services.registry.skills import sync_skills
        from app.services.workbench_service import _select_skills_for_run

        skills = sync_skills(self._db)
        skill_map = {skill.skill_id: skill for skill in skills}
        selected = _select_skills_for_run(
            self._db,
            skill_map,
            list(skill_ids),
            run_mode=run_mode,
            preset_id=preset_id,
            raw_skills=skills,
        )
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
        execution_audit: StageMapping | None = None,
    ) -> None:
        from app.services.workbench_service import _persist_run_start

        _persist_run_start(
            self._db,
            run_id,
            dict(params),
            list(selected_skills),
            request=request,
            execution_version=execution_version,
            execution_audit=execution_audit,
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
        if as_of_date:
            # Historical replay must not read the near-term stock_quotes cache.
            # Build point-in-time quotes from daily_kbars/daily_basic instead so
            # older dates remain reproducible and causally bounded.
            from app.services.backtest.historical_universe import load_historical_universe
            from app.services.backtest.stock_metadata import StockMetadataHistoryProvider

            stocks, snapshot = load_historical_universe(
                self._db,
                as_of_date,
                stock_metadata_provider=StockMetadataHistoryProvider(self._db),
            )
            count = len(stocks)
            return (
                tuple(stocks),
                snapshot,
                {
                    "source": "historical_pool",
                    "operation": "quotes",
                    "status": "success" if count else "empty",
                    "requested_count": count,
                    "returned_count": count,
                    "empty_count": 0 if count else 1,
                    "failed_count": 0,
                    "failure_policy": "raise",
                },
            )

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

    def __init__(self, db: Session, run_id: str) -> None:
        self._db = db
        self._run_id = run_id

    def persist_snapshot(self, snapshot: StageMapping) -> None:
        from app.services.workbench_service import _persist_market
        _persist_market(self._db, dict(snapshot))
        # 同一交易日的 run 共享 snapshot_id；立即提交，免得后到的 run 在等共享行情写入锁时
        # 让别的 run 卡在这一行的唯一键锁上
        self._db.commit()

    def persist_stocks_and_bars(
        self,
        stocks: Sequence[StageMapping],
        *,
        persist_bars: bool,
    ) -> None:
        from app.services.workbench_service import _persist_stocks_and_bars

        # 多个 run 会同时写同一批全市场 stocks/daily_kbars 行。按顺序排序或逐批提交都挡不住：
        # 唯一索引上的 upsert 带间隙锁，按行情列分组写入又会多次扫过代码区间，实测分别报
        # 1205 锁等待超时与 1213 死锁（L-157）。用 MySQL 命名锁让这段写入跨进程串行，
        # 后到的 run 排队等前一个写完提交。锁挂在独立连接上，与 run 会话的连接归还无关。
        with self._db.get_bind().connect() as lock_conn:
            acquired = lock_conn.scalar(
                text("SELECT GET_LOCK(:name, :timeout)"),
                {"name": _SHARED_MARKET_WRITE_LOCK, "timeout": _SHARED_MARKET_WRITE_LOCK_TIMEOUT_SECONDS},
            )
            if acquired != 1:
                raise RuntimeError(
                    f"等待共享行情写入锁超过 {_SHARED_MARKET_WRITE_LOCK_TIMEOUT_SECONDS} 秒，"
                    "另一个 run 仍在写全市场日K"
                )
            try:
                _persist_stocks_and_bars(self._db, list(stocks), persist_bars=persist_bars)
                self._db.commit()
            finally:
                lock_conn.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": _SHARED_MARKET_WRITE_LOCK})

    def commit_shared_market_facts(self) -> None:
        self._db.commit()

    def persist_run_market_context(
        self,
        snapshot: StageMapping,
        *,
        universe_count: int,
    ) -> None:
        from app.models.tables import RecommendationRun
        from app.services.workbench_service import _dumps, _slim_market_snapshot

        run = self._db.get(RecommendationRun, self._run_id)
        if run is None:
            raise RuntimeError(f"Run {self._run_id} 不存在")
        run.trade_date = str(snapshot.get("trade_date") or run.trade_date)
        run.data_as_of = str(snapshot.get("trade_date") or "") or None
        run.source_snapshot_id = str(snapshot.get("snapshot_id") or "") or None
        run.universe_count = universe_count
        run.market_snapshot_json = _dumps(_slim_market_snapshot(dict(snapshot)))
        self._db.flush()


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
        as_of_date: str | None,
    ) -> Any:
        from app.services.auxiliary.application import run_auxiliary_application

        return run_auxiliary_application(
            self._db,
            instruments=list(instruments),
            trade_date=trade_date,
            refresh=refresh,
            as_of_date=as_of_date,
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
        *,
        auxiliary_views: StageMapping | None = None,
    ) -> tuple[tuple[StageMapping, ...], tuple[Any, ...]]:
        from app.services.feature_builder import build_features
        from app.services.research_context import build_research_contexts
        from app.services.workbench_service import _load_calibrated_dsa_weights

        features = build_features(
            list(stocks),
            dict(snapshot),
            _load_calibrated_dsa_weights(self._db),
            db=self._db,
            auxiliary_views=auxiliary_views,
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
        prefilter_audit: StageMapping,
    ) -> tuple[tuple[StageMapping, ...], tuple[StageMapping, ...], StageMapping, StageMapping]:
        from app.engine.strategy import build_registry_from_skills, StrategyContext
        from app.services.runs.skill_gate import (
            build_skill_rule_gate,
            finalize_skill_gate_audit,
            resolve_skill_review_concurrency,
        )
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
                elif prefilter_source in ("fill", "fallback"):
                    # fill/fallback 股票未挂 pass 技能列表；若对全部技能 evaluate，
                    # universe 失败会变成 hard_veto="不在策略股票池" 并在聚合层把整卡
                    # final_score 置 0。与 skill_pass 对齐：只跑通过自身 prefilter 的技能。
                    from app.engine.strategy_runner import evaluate_skill_prefilter

                    passed, _reason = evaluate_skill_prefilter(skill_obj, stock, stage="market")
                    if not passed:
                        signal = None
                    else:
                        ctx = StrategyContext(
                            main_industries=snapshot.get("main_industries"),
                            skip_universe_check=True,
                        )
                        signal = (
                            strategy_registry.get(skill_obj.skill_id).evaluate(stock, ctx)
                            if skill_obj.executable
                            else None
                        )
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
            skill_review_concurrency=resolve_skill_review_concurrency(dict(params), settings),
            skill_demo_limited=explicit_review_limit_skipped > 0,
            prefilter_audit=dict(prefilter_audit),
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
        from app.core.config import get_settings

        if agent_review_disabled:
            return tuple(
                {**entry, "review_skill_ids": [], "agent_review_disabled": True, "skill_reviews": []}
                for entry in candidates
            )

        request_limiter = asyncio.Semaphore(concurrency)

        async def _process(entry: dict[str, Any]) -> dict[str, Any]:
            stock = entry["stock"]
            review_skill_ids = set(entry.get("review_skill_ids") or [])
            review_skills = [
                selected["skill"]
                for selected in selected_skills
                if selected["skill_id"] in review_skill_ids
            ]
            skill_reviews = await run_skill_reviews(
                stock=stock,
                skills=review_skills,
                snapshot=dict(snapshot),
                rule_signals_by_skill=entry["rule_signals"],
                session_id=session_id,
                request_limiter=request_limiter,
            )
            return {**entry, "skill_reviews": skill_reviews}

        entries = [dict(entry) for entry in candidates]
        timeout_seconds = float(get_settings().agent_review_stage_timeout_seconds or 0)
        if timeout_seconds <= 0:
            results = []
            tasks = [asyncio.create_task(_process(entry)) for entry in entries]
            for task in asyncio.as_completed(tasks):
                results.append(await task)
            return tuple(results)

        # LLM 复核是可选标记，禁止阻塞推荐管线：整批超过时限即放弃未完成候选，
        # 未完成项按「未复核」返回（skill_reviews 为空），既不否决也不改分数。
        ordered_tasks = [asyncio.create_task(_process(entry)) for entry in entries]
        done, pending = await asyncio.wait(ordered_tasks, timeout=timeout_seconds)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        results: list[dict[str, Any]] = []
        for index, task in enumerate(ordered_tasks):
            if task in done and not task.cancelled() and task.exception() is None:
                results.append(task.result())
            else:
                results.append(
                    {
                        **entries[index],
                        "skill_reviews": [],
                        "agent_review_stage_timeout": True,
                    }
                )
        return tuple(results)


class ProductionCostRecording(CostRecordingPort):
    """Delegate cost recording to drain_api_costs + record_api_call."""

    def __init__(self, db: Session, run_id: str) -> None:
        self._db = db
        self._run_id = run_id

    def record_costs(self, run_id: str) -> tuple[StageMapping, ...]:
        from app.agents.agno_skill_agent import drain_api_costs
        from app.services.cost_service import record_api_call

        api_costs = drain_api_costs()
        for cost in api_costs:
            record_api_call(self._db, run_id=run_id, **cost)
        return tuple(api_costs)


# ---------------------------------------------------------------------------
# Aggregation adapter
# ---------------------------------------------------------------------------

class ProductionRecommendationAssembly(RecommendationAssemblyPort):
    """Delegate card building and pool rules to existing helpers."""

    def __init__(self, db: Session, run_id: str) -> None:
        self._db = db
        self._run_id = run_id

    def aggregate(
        self,
        *,
        candidate_results: Sequence[StageMapping],
        selected_skills: Sequence[StageMapping],
        snapshot: StageMapping,
        params: StageMapping,
        card_filter: "callable | None" = None,
    ) -> tuple[tuple[StageMapping, ...], tuple[StageMapping, ...], StageMapping]:
        from app.services.workbench_service import (
            _build_recommendation_card,
            _recent_recommended_codes,
            _same_day_recommended_codes,
            _same_day_industry_counts,
            _load_calibrated_agg_weights,
            _drop_as_repeat_card,
        )
        from dataclasses import asdict
        from app.engine.production_policy import (
            DEFAULT_PRODUCTION_SELECTION_POLICY,
            SelectionPolicyContext,
            evaluate_selection_policy,
        )
        from app.services.feature_builder import market_regime_score

        from app.services.standalone_strategies import (
            standalone_selection_policy,
            standalone_skill_id,
        )

        skill_map = {s["skill_id"]: s["skill"] for s in selected_skills}
        # 单策略独立运行（每日每个策略一个 formal run）：去重/行业集中/出票上限只在同策略内生效，
        # 关闭组合专属的共识闸门。多策略 run（旧组合口径/研究回放）行为不变。
        standalone_sid = standalone_skill_id(selected_skills)
        recent_codes = _recent_recommended_codes(
            self._db, str(snapshot.get("trade_date") or ""), skill_scope=standalone_sid,
        )
        same_day_codes = _same_day_recommended_codes(
            self._db, str(snapshot.get("trade_date") or ""), self._run_id, skill_scope=standalone_sid,
        )
        market_score = market_regime_score(dict(snapshot)) or 50.0  # 防御：市场分未就绪时回退中性值
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
        same_day_industry_counts = _same_day_industry_counts(
            self._db, str(snapshot.get("trade_date") or ""), self._run_id, skill_scope=standalone_sid,
        )
        _sel_date = str(snapshot.get("trade_date") or "")
        if _sel_date:
            # L-093/L-109：附加当日全市场 ivol20 / ivol_dn20 横截面分位，供 v3 强牛×高特质
            # 波动否决、v4 弱势×低特质下行波动否决使用；规则关闭（v1/v2/v3）时这两个字段
            # 不影响任何选择，保持逐位可复现。读取失败（如迁移 0018/0019 尚未执行、列暂缺）
            # 时保守降级为不带风险字段，对应否决自然不触发，绝不因风险特征缺失而中断整轮选股。
            try:
                from app.services.risk_features import (
                    load_ivol20_pct_map, attach_ivol20_pct,
                    load_ivol_dn20_pct_map, attach_ivol_dn20_pct,
                )
                _ivol_map = load_ivol20_pct_map(self._db, [_sel_date])
                attach_ivol20_pct(scored_cards, _sel_date, _ivol_map)
                _ivoldn_map = load_ivol_dn20_pct_map(self._db, [_sel_date])
                attach_ivol_dn20_pct(scored_cards, _sel_date, _ivoldn_map)
            except Exception as _ivol_exc:  # noqa: BLE001
                logger.warning("风险特征分位附加失败，本轮保守不做风险否决: %s", _ivol_exc)
        # L-126 自适应共识闸门：按"已结算的近 K 日多背书边际"选当日政策版本
        # （edge>0 → 正向分支；edge<−1.5 → 反向分支；其余/样本不足 → 默认中性分支）。
        # 未启用该机制（`adaptive_consensus_enabled=False`）时原样返回默认政策，逐位不变。
        from app.services.consensus_edge import resolve_adaptive_selection_policy

        # 单策略端到端研究（`--only-skill-cards`）：在**policy 之前**按"该技能是否发出非否决信号"过滤候选卡，
        # 其它技能的信号仍留在卡上（共识闸门/多策略背书不受影响）——这与面板口径 `only:<sid>` 完全一致。
        if card_filter is not None:
            scored_cards = [c for c in scored_cards if card_filter(c)]
        _selection_policy = resolve_adaptive_selection_policy(
            self._db, str(snapshot.get("trade_date") or ""), DEFAULT_PRODUCTION_SELECTION_POLICY
        )
        if standalone_sid:
            _selection_policy = standalone_selection_policy(
                DEFAULT_PRODUCTION_SELECTION_POLICY,
                standalone_sid,
                int(params.get("output_count") or 0) or 3,
            )
        policy_result = evaluate_selection_policy(
            scored_cards,
            SelectionPolicyContext(
                output_count=int(params.get("output_count") or 0),
                score_line=float(params.get("score_line") or 72.0),
                market_snapshot=dict(snapshot),
                risk_template=str(params.get("risk_template") or "balanced"),
                same_day_industry_counts=same_day_industry_counts,
                same_day_recommendation_count=len(same_day_codes),
            ),
            _selection_policy,
        )
        formal_cards = list(policy_result.recommendations)
        pipeline_updates: dict[str, Any] = {
            "watchlist": list(policy_result.watchlist),
            "degraded_watchlist": list(policy_result.degraded_watchlist),
            "production_policy_fingerprint": policy_result.policy_fingerprint,
            "policy_verdict_summary": dict(policy_result.verdict_summary),
            "policy_verdicts": [asdict(item) for item in policy_result.verdicts],
        }
        return tuple(formal_cards), tuple(scored_cards), pipeline_updates


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
    ) -> SealedRecommendationCollection:
        from app.services.runs.collection_seal import seal_recommendation_collection
        from app.services.runs.persistence import persist_recommendation_cards
        from app.services.workbench_service import _dumps

        saved = persist_recommendation_cards(
            self._db,
            run_id=run_id,
            trade_date=trade_date,
            cards=list(cards),
            dump_json=_dumps,
        )
        self._db.flush()
        seal = seal_recommendation_collection(self._db, run_id)
        return SealedRecommendationCollection(
            recommendations=tuple(saved),
            recommendation_row_count=int(seal.recommendation_row_count),
            collection_digest=str(seal.collection_digest),
            side_effect_key=str(seal.checkpoint_side_effect_key),
        )


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
