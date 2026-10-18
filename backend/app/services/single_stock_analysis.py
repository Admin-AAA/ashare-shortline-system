"""单股票分析服务：以单只股票为输入执行正式推荐评分管线。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.trading_calendar import normalize_trade_date, latest_allowed_bar_date
from app.models.tables import Preset, RecommendationRun, Stock
from app.services.registry.default_strategy import DEFAULT_SINGLE_STRATEGY_ID
from app.services.evidence.validation import (
    canonical_historical_reconstruction_run_ids,
    canonical_live_forward_run_ids,
)

logger = logging.getLogger(__name__)


def _resolve_analysis_market_snapshot(
    db: Session,
    trade_date: str,
    quote: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """为 analyze-stock 解析市场快照。

    优先：同 trade_date 最近 completed formal run 的 market_snapshot_json
    （全市场 sample_count/limit_up/regime/main_industries，与推荐管线同源）。
    回退：最小单股 snapshot（source=minimal_fallback；LLM 可能因 regime=未知 误判）。

    返回 (snapshot, source_tag)。
    """
    from app.services.market_data import _build_quote_snapshot

    try:
        live_run_ids = canonical_live_forward_run_ids(db, trade_date=trade_date)
        historical_run_ids = canonical_historical_reconstruction_run_ids(
            db,
            trade_date=trade_date,
        )
        selected_scope = "live_forward" if live_run_ids else "historical_replay"
        eligible_run_ids = live_run_ids or historical_run_ids
        raw = None
        if eligible_run_ids:
            raw = db.execute(
                select(RecommendationRun.market_snapshot_json)
                .where(
                    RecommendationRun.run_id.in_(eligible_run_ids),
                    RecommendationRun.market_snapshot_json.is_not(None),
                    RecommendationRun.market_snapshot_json != "",
                    RecommendationRun.market_snapshot_json != "{}",
                )
                .order_by(RecommendationRun.started_at.desc())
                .limit(1)
            ).scalar_one_or_none()
    except Exception as exc:  # pragma: no cover - 防御  # noqa: BLE001
        logger.warning("load formal market snapshot failed for %s: %s", trade_date, exc)
        raw = None
        selected_scope = ""

    if raw:
        try:
            snap = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            snap = None
        if isinstance(snap, dict) and int(snap.get("sample_count") or 0) >= 100:
            # 确保 trade_date 对齐分析日
            snap.setdefault("trade_date", trade_date)
            breadth = snap.get("breadth") if isinstance(snap.get("breadth"), dict) else {}
            snap["breadth"] = breadth
            return snap, (
                "formal_run_snapshot"
                if selected_scope == "live_forward"
                else "historical_replay_snapshot"
            )

    # fallback：最小单股 snapshot（保留旧行为，但打标）
    snapshot = _build_quote_snapshot([quote], "single", "single", [])
    snapshot["main_industries"] = []
    snapshot["breadth"] = snapshot.get("breadth") or {}
    snapshot["breadth"]["regime"] = "未知"
    snapshot["trade_date"] = trade_date
    snapshot["sample_count"] = int(snapshot.get("sample_count") or 1)
    return snapshot, "minimal_fallback"


def analyze_stock(
    db: Session,
    code: str,
    *,
    trade_date: str | None = None,
    include_llm: bool = False,
    skill_id: str = DEFAULT_SINGLE_STRATEGY_ID,
    preset_id: str | None = None,
) -> dict[str, Any]:
    """以单只股票为输入，原样执行正式推荐管线的评分阶段。"""
    t0 = time.perf_counter()
    if not trade_date:
        trade_date = latest_allowed_bar_date()
    trade_date = normalize_trade_date(trade_date)
    stock = db.scalar(select(Stock).where(Stock.code == code))
    if not stock:
        return {"status": "error", "message": f"股票 {code} 不存在"}
    request, preset = _single_stock_run_request(
        db,
        trade_date=trade_date,
        include_llm=include_llm,
        skill_id=skill_id,
        preset_id=preset_id,
    )
    run_id = f"single-stock-{code}-{uuid.uuid4().hex[:10]}"
    universe = _SingleStockMarketUniverse(db, code)
    data = asyncio.run(_execute_single_stock_scoring_pipeline(db, code, run_id, request, universe))

    source_stock = next((dict(item) for item in data.stocks if item.get("code") == code), {})
    feature = next((dict(item) for item in data.features if item.get("code") == code), source_stock)
    scored_card = next((dict(item) for item in data.scored_cards if item.get("code") == code), None)
    recommended_card = next((dict(item) for item in data.cards if item.get("code") == code), None)
    candidate = next((dict(item) for item in data.candidate_results if (item.get("stock") or {}).get("code") == code), None)
    skill_reviews = list((candidate or {}).get("skill_reviews") or [])
    normalized_reviews = [_serialize_skill_review(item) for item in skill_reviews]
    final_score = float(scored_card["final_score"]) if scored_card is not None else None
    profile = feature.get("dsa_profile") or feature
    feature_snapshot = (scored_card or {}).get("feature_snapshot") or feature.get("feature_snapshot") or feature
    snapshot = dict(data.snapshot or {})
    pipeline_decision = _pipeline_decision(data, code)

    result: dict[str, Any] = {
        "status": "ok",
        "code": code,
        "name": stock.name,
        "trade_date": str(snapshot.get("trade_date") or trade_date),
        "bar_count": len(source_stock.get("_bars") or []),
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        "price": feature.get("price") or feature.get("close"),
        "final_score": final_score,
        "recommended": recommended_card is not None,
        "pipeline_decision": pipeline_decision,
        # formal_run_snapshot | historical_replay_snapshot | pipeline_full_universe
        "market_snapshot_source": universe.snapshot_source,
        "market_snapshot_summary": {
            "sample_count": snapshot.get("sample_count"),
            "limit_up_count": snapshot.get("limit_up_count"),
            "limit_down_count": snapshot.get("limit_down_count"),
            "regime": (snapshot.get("breadth") or {}).get("regime"),
            "up_ratio": (snapshot.get("breadth") or {}).get("up_ratio"),
            "hot_theme": (snapshot.get("breadth") or {}).get("hot_theme"),
            "main_industries_n": len(snapshot.get("main_industries") or []),
        },
        "dsa_profile": {
            "macd_status": profile.get("macd_status"),
            "macd_dif": profile.get("macd_dif"),
            "macd_dea": profile.get("macd_dea"),
            "rsi_12": profile.get("rsi_12"),
            "bias_ma5": profile.get("bias_ma5"),
            "bias_ma10": profile.get("bias_ma10"),
            "trend_status": profile.get("trend_status"),
            "volume_status": profile.get("volume_status"),
        },
        "scoring": {
            "dsa_signal_score": feature.get("dsa_signal_score"),
            "dsa_trend_score": feature.get("dsa_trend_score"),
            "dsa_bias_score": feature.get("dsa_bias_score"),
            "dsa_volume_score": feature.get("dsa_volume_score"),
            "dsa_support_score": feature.get("dsa_support_score"),
            "dsa_macd_score": feature.get("dsa_macd_score"),
            "dsa_rsi_score": feature.get("dsa_rsi_score"),
            "risk_score": feature.get("risk_score"),
        },
        "entry_rules": {
            "ideal_buy": feature.get("ideal_buy") or feature_snapshot.get("ideal_buy"),
            "secondary_buy": feature.get("secondary_buy") or feature_snapshot.get("secondary_buy"),
            "stop_loss": (scored_card or {}).get("stop_loss") or feature_snapshot.get("stop_loss"),
            "take_profit": (scored_card or {}).get("take_profit") or feature_snapshot.get("take_profit"),
        },
        "pipeline": {
            "decision": pipeline_decision,
            "recommended": recommended_card is not None,
            "preset_id": request.preset_id,
            "preset_name": preset.name if preset else None,
            "score_line": request.score_line,
            "selected_skill_count": len(data.selected_skills),
            "skill_gate_passed": bool(data.skill_candidates),
            "reviewed": bool(skill_reviews),
            "filter_audit": data.filter_audit,
            "pipeline_audit": data.pipeline_audit,
        },
    }
    if include_llm:
        result["llm_analysis"] = _single_stock_llm_analysis(
            skill_reviews=skill_reviews,
            normalized_reviews=normalized_reviews,
            scored_card=scored_card,
            preset=preset,
            request=request,
            api_costs=list(data.api_costs),
            decision=pipeline_decision,
        )

    # 管线审计里含 tuple 等非 JSON 原生类型，ContractRouter 的 JsonResponse(JsonValue)
    # 会拒绝 → 中间件吞掉异常返回 500（历史已落库但前端永远拿到 500）。统一转成 JSON 原生结构。
    result = _json_safe(result)
    _persist_single_stock_history(db, code, trade_date, result)
    return result


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


class _SingleStockMarketUniverse:
    """生产市场 Universe 的单代码视图；市场快照仍保留全市场口径。"""

    def __init__(self, db: Session, code: str) -> None:
        from app.services.runs.stages.adapters import ProductionMarketUniverse

        self._delegate = ProductionMarketUniverse(db)
        self._db = db
        self._code = code
        self.snapshot_source = "pipeline_full_universe"

    def load_universe(
        self,
        *,
        data_source: str,
        as_of_date: str | None,
    ) -> tuple[tuple[dict[str, Any], ...], dict[str, Any], dict[str, Any]]:
        stocks, snapshot, audit = self._delegate.load_universe(
            data_source=data_source,
            as_of_date=as_of_date,
        )
        selected = tuple(dict(stock) for stock in stocks if str(stock.get("code") or "") == self._code)
        if not selected:
            raise ValueError(f"股票 {self._code} 不在推荐管线的市场数据 Universe 中")
        scoped_audit = dict(audit)
        scoped_audit["single_stock_code"] = self._code
        scoped_audit["full_universe_count"] = len(stocks)
        live_snapshot = dict(snapshot)
        trade_date = str(live_snapshot.get("trade_date") or as_of_date or "")
        resolved, source = _resolve_analysis_market_snapshot(self._db, trade_date, dict(selected[0]))
        if source == "minimal_fallback":
            # 没有同日正式 run 快照：保留管线按全市场 Universe 现算的快照（不是单股最小快照）。
            self.snapshot_source = "pipeline_full_universe"
            out_snapshot = live_snapshot
        else:
            # 优先当日正式 run 的 market_snapshot_json，保证单股评分与当日正式出票同一市场口径。
            self.snapshot_source = source
            out_snapshot = resolved
        scoped_audit["market_snapshot_source"] = self.snapshot_source
        return selected, out_snapshot, scoped_audit


class _DrainOnlyCostRecording:
    """单股分析不创建 RecommendationRun，只排空并返回本次 API 用量。"""

    def record_costs(self, run_id: str) -> tuple[dict[str, Any], ...]:
        from app.agents.agno_skill_agent import drain_api_costs

        return tuple(drain_api_costs())


def _single_stock_scoring_stages(
    db: Session,
    *,
    code: str,
    run_id: str,
    market_universe: "_SingleStockMarketUniverse | None" = None,
) -> tuple[Any, ...]:
    """通过正式管线的唯一组装入口生成同一组评分 Stage。"""
    from app.services.runs.stages.adapters import (
        ProductionAuxiliaryApplication,
        ProductionExecutionVersion,
        ProductionFeatureBuild,
        ProductionMarketScope,
        ProductionRecommendationAssembly,
        ProductionReviewExecutor,
        ProductionSkillResolution,
        ProductionStrategyEvaluation,
    )
    from app.services.runs.stages.assembly import assemble_scoring_pipeline_stages

    return assemble_scoring_pipeline_stages(
        skill_resolution=ProductionSkillResolution(db),
        execution_version=ProductionExecutionVersion(),
        market_universe=market_universe or _SingleStockMarketUniverse(db, code),
        market_scope=ProductionMarketScope(db),
        auxiliary_application=ProductionAuxiliaryApplication(db),
        feature_build=ProductionFeatureBuild(db),
        strategy_evaluation=ProductionStrategyEvaluation(db),
        review_executor=ProductionReviewExecutor(),
        cost_recording=_DrainOnlyCostRecording(),
        recommendation_assembly=ProductionRecommendationAssembly(db, run_id),
    )


def _single_stock_run_request(
    db: Session,
    *,
    trade_date: str,
    include_llm: bool,
    skill_id: str,
    preset_id: str | None,
) -> tuple[Any, Preset | None]:
    from app.services.runs.contracts import parse_run_request
    from app.services.workbench_service import _merge_run_params

    preset = db.get(Preset, preset_id) if preset_id else None
    if preset_id and preset is None:
        raise ValueError(f"预设 {preset_id} 不存在")
    payload: dict[str, Any] = {
        "preset_id": preset_id,
        "trade_date_override": trade_date,
        "disable_agent_review": not include_llm,
        "run_name": f"单股推荐 · {skill_id if not preset else preset.name}",
    }
    if preset is None:
        payload["single_skill_id"] = skill_id
    params = _merge_run_params(db, preset, payload)
    params["run_mode"] = "formal"
    return parse_run_request(params), preset


async def _execute_single_stock_scoring_pipeline(
    db: Session,
    code: str,
    run_id: str,
    request: Any,
    market_universe: "_SingleStockMarketUniverse | None" = None,
) -> Any:
    from app.agents.session_registry import AgentSessionRegistry
    from app.db.session import SessionLocal
    from app.services.runs.coordinator import RunContext, WorkerLease
    from app.services.runs.stages.contracts import StageData
    from app.services.runs.stages.review import ReviewStage

    context = RunContext(
        run_id=run_id,
        request=request,
        worker_lease=WorkerLease(run_id=run_id, token="single-stock-analysis", revision=0),
    )
    data = StageData()
    registered = False
    universe = market_universe or _SingleStockMarketUniverse(db, code)
    try:
        for stage in _single_stock_scoring_stages(db, code=code, run_id=run_id, market_universe=universe):
            if isinstance(stage, ReviewStage) and not registered:
                AgentSessionRegistry.register(
                    session_id=f"sess_{run_id}",
                    session_factory=lambda: SessionLocal(),
                    skills=[item["skill"] for item in data.selected_skills],
                    snapshot=dict(data.snapshot or {}),
                )
                registered = True
            output = await stage.execute(context, data) if isinstance(stage, ReviewStage) else stage.execute(context, data)
            data = output.data
        return data
    finally:
        if registered:
            AgentSessionRegistry.unregister(f"sess_{run_id}")


def _pipeline_decision(data: Any, code: str) -> str:
    if any(item.get("code") == code for item in data.cards):
        return "recommended"
    if any(item.get("code") == code for item in data.scored_cards):
        return "scored_not_recommended"
    if any((item.get("stock") or {}).get("code") == code for item in data.skill_candidates):
        return "reviewed_not_scored"
    if any((item.get("stock") or {}).get("code") == code for item in data.prelim_entries):
        return "rejected_by_skill_gate"
    return "rejected_before_scoring"


def _serialize_skill_review(item: dict[str, Any]) -> dict[str, Any]:
    review = item.get("review") or {}
    return {
        "skill_id": item.get("skill_id"),
        "skill_name": item.get("skill_name"),
        "status": "error" if item.get("error") else "ok",
        "error": item.get("error"),
        "score": review.get("score"),
        "stance": review.get("stance"),
        "confidence": review.get("confidence"),
        "summary": review.get("summary"),
        "action": review.get("action"),
        "supports": review.get("supports") or review.get("evidence") or [],
        "risks": review.get("risks") or [],
    }


def _single_stock_llm_analysis(
    *,
    skill_reviews: list[dict[str, Any]],
    normalized_reviews: list[dict[str, Any]],
    scored_card: dict[str, Any] | None,
    preset: Preset | None,
    request: Any,
    api_costs: list[dict[str, Any]],
    decision: str,
) -> dict[str, Any]:
    valid = [item for item in skill_reviews if item.get("review") and not item.get("error")]
    if valid:
        scores = [float(item["review"].get("score", 50)) for item in valid]
        confidences = [float(item["review"].get("confidence", 0.5)) for item in valid]
        agent_score = sum(score * confidence for score, confidence in zip(scores, confidences)) / max(sum(confidences), 0.01)
    else:
        agent_score = None
    return {
        "status": "ok" if valid else "skipped",
        "summary": None if valid else f"正式推荐管线未触发 LLM 深审：{decision}",
        "preset_id": request.preset_id,
        "preset_name": preset.name if preset else None,
        "risk_template": request.risk_template,
        "final_score": scored_card.get("final_score") if scored_card else None,
        "component_scores": scored_card.get("component_scores", {}) if scored_card else {},
        "agent_review_score": round(agent_score, 2) if agent_score is not None else None,
        "skill_reviews": normalized_reviews,
        "api_costs": api_costs,
        "total_tokens_est": sum((item.get("prompt_tokens_est") or 0) + (item.get("completion_tokens_est") or 0) for item in api_costs),
        "total_tokens_real": sum((item.get("prompt_tokens") or 0) + (item.get("completion_tokens") or 0) for item in api_costs),
        "total_cache_hit_tokens": sum(item.get("cache_hit_tokens") or 0 for item in api_costs),
        "prompt_cache_active": any((item.get("cache_hit_tokens") or 0) > 0 for item in api_costs if item.get("status") == "success"),
    }


def _persist_single_stock_history(db: Session, code: str, trade_date: str, result: dict[str, Any]) -> None:
    """落库单股票评分历史（复用 stock_matrix_history 表，analysis_type="single_stock"）。

    失败仅记日志、不阻塞主流程（与 stock_matrix_service 同款约定）。
    """
    try:
        from app.models.tables import StockMatrixHistory

        final_score = _resolve_single_stock_final_score(result)

        # skill_count：preset 模式取有效 skill 数，单 skill 模式=1
        llm = result.get("llm_analysis")
        skill_count = 1
        if llm and isinstance(llm.get("skill_reviews"), list):
            valid = [r for r in llm["skill_reviews"] if r.get("status") == "ok"]
            skill_count = len(valid) if valid else len(llm["skill_reviews"])

        db.add(StockMatrixHistory(
            code=code,
            trade_date=trade_date,
            analysis_type="single_stock",
            final_score=final_score,
            skill_count=skill_count,
            result_json=json.dumps(result, ensure_ascii=False, default=str),
            elapsed_ms=result.get("elapsed_ms"),
        ))
        db.commit()
    except Exception as hist_exc:  # noqa: BLE001 - logged or recorded, non-blocking by design
        logger.warning("写入单股票评分历史失败 (code=%s): %s", code, hist_exc)
        db.rollback()


def _resolve_single_stock_final_score(result: dict[str, Any]) -> float | None:
    """返回单股记录唯一总分，口径与页面实际评分一致且限定为 0–100。

    preset 模式优先使用推荐管线聚合分；否则使用已经归一化的
    ``dsa_signal_score``。后者本身就是六个技术子分的总分，不能再次求和。
    """
    if "final_score" in result and result["final_score"] is None:
        return None
    explicit = result.get("final_score")
    llm = result.get("llm_analysis") or {}
    value = explicit if explicit is not None else llm.get("final_score")
    if value is None:
        value = (result.get("scoring") or {}).get("dsa_signal_score")
    if value is None:
        return None
    try:
        return round(max(0.0, min(100.0, float(value))), 2)
    except (TypeError, ValueError):
        return None
