"""S7 运行编排纯计算 golden-master。

本文件不创建 ORM Session，也不调用市场、LLM 或 MySQL 专属持久化路径；它锁定
运行编排在纯计算边界的候选评分、分数线、审计和 API 投影契约。
"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from app.engine.aggregator import apply_pool_rules
from app.services.recommendations.projection import serialize_recommendation, serialize_run_summary
from app.services.runs.filter_audit import build_filter_audit, build_pipeline_audit
from app.services.runs.skill_gate import build_skill_rule_gate
from app.services.workbench_service import _build_recommendation_card


def _market_snapshot() -> dict:
    return {
        "snapshot_id": "golden-snapshot-20260601",
        "trade_date": "2026-06-01",
        "sample_count": 2,
        "up_count": 1,
        "down_count": 1,
        "limit_up_count": 0,
        "limit_down_count": 0,
        "main_industries": ["半导体"],
        "breadth": {
            # This golden fixture exercises the successful recommendation
            # projection. Oscillation is a production hard-veto regime and is
            # covered by dedicated policy tests, so it cannot be used here.
            "regime": "未知",
            "data_source": "in_memory",
            "unified_quote_total": 2,
            "base_filter_eligible_count": 2,
            "scoped_candidate_count": 2,
            "prefilter_audit": {"prefilter_candidate_count": 1},
            "candidate_limit_config": {
                "market_candidate_count": 2,
                "lookahead_multiplier": 2,
                "lookahead_count": 2,
            },
        },
    }


def _prelim_entry() -> dict:
    stock = {
        "code": "600001",
        "name": "黄金样本",
        "price": 12.34,
        "close": 12.34,
        "industry": "半导体",
        "pct": 3.2,
        "amount_yi": 8.6,
        "turnover_rate": 6.1,
        "chg20": 18.0,
        "pos60": 68.0,
        "data_completeness_score": 92.0,
        "evidence_quality_score": 88.0,
        "data_quality": {"source_breakdown": {"OBSERVED": 12}, "missing_fields": []},
    }
    signal = {
        "strategy_id": "golden-momentum",
        "strategy_name": "黄金动量",
        "score": 86.0,
        "confidence": 0.82,
        "phase": "continuation",
        "setup_match": "突破确认",
        "entry_rule": "放量突破后回踩不破",
        "invalid_rule": "跌破五日线",
        "evidence": ["量能放大", "主线共振"],
        "risk_flags": [],
        "hard_veto": False,
    }
    return {
        "stock": stock,
        "rule_signals": {"golden-momentum": signal},
        "prelim_score": 86.0,
    }


def _normalize(card: dict, pipeline: dict, filter_audit: dict, recommendation: dict, run: dict) -> dict:
    score_line = card["score_line_meta"]
    audit_row = filter_audit["rows"][0]
    return {
        "candidate": {
            "code": card["code"],
            "final_score": round(card["final_score"], 2),
            "consensus_score": round(card["consensus_score"], 2),
            "setup_type": card["setup_type"],
            "strategy_ids": [signal["strategy_id"] for signal in card["strategy_signals"]],
            "evidence": card["strategy_signals"][0]["evidence"],
            "risk_flags": card["risk_flags"],
            "effective_score_line": score_line["effective_score_line"],
            "passed": score_line["passed"],
        },
        "pipeline": {
            "actual_scan_count": pipeline["actual_scan_count"],
            "skill_input_count": pipeline["skill_input_count"],
            "skill_gate_candidate_count": pipeline["skill_gate_candidate_count"],
            "skill_agent_call_count": pipeline["skill_agent_call_count"],
            "effective_output_count": pipeline["effective_output_count"],
        },
        "filter_audit": {
            "stage": audit_row["final_stage"],
            "status": audit_row["final_status"],
            "reason": audit_row["final_reason"],
            "trace_stages": [item["stage"] for item in audit_row["trace"]],
            "reason_counts": filter_audit["reason_counts"],
        },
        "recommendation_projection": {
            key: recommendation[key]
            for key in (
                "recommendation_id",
                "run_id",
                "code",
                "final_score",
                "watch_status",
                "llm_stance",
                "llm_delta",
                "preset_id",
                "run_name",
            )
        },
        "run_projection": {
            key: run[key]
            for key in (
                "run_id",
                "preset_id",
                "status",
                "progress_pct",
                "is_stale",
                "skill_count",
                "hit_rate",
            )
        },
    }


def test_run_workbench_pure_computation_matches_golden_master():
    snapshot = _market_snapshot()
    selected_skills = [{"skill_id": "golden-momentum", "weight": 1.0}]
    gate_settings = SimpleNamespace(
        market_skill_rule_gate_min_score=58,
        market_skill_rule_gate_min_per_skill=0,
        market_skill_rule_gate_min_total=0,
        market_skill_rule_gate_max_per_skill=0,
        market_skill_rule_gate_max_total=20,
    )
    gate_result = build_skill_rule_gate([_prelim_entry()], selected_skills, gate_settings)
    gated_entry = gate_result["skill_candidates"][0]
    reviews = [{
        "skill_id": "golden-momentum",
        "skill_name": "黄金动量",
        "review": {
            "stance": "support",
            "score": 88.0,
            "score_delta": 8.0,
            "confidence": 0.9,
            "evidence": ["趋势延续", "量价配合"],
            "risks": [],
            "summary": "突破后承接稳定",
            "action": "轻仓试错",
        },
        "hallucination_check": {"flags": [], "severity": "low"},
        "error": None,
    }]
    card = _build_recommendation_card(
        gated_entry,
        reviews,
        skill_map={},
        selected_skills=selected_skills,
        market_score=72.0,
        risk_template="balanced",
        calibrated_weights=None,
        snapshot=snapshot,
    )
    recommended_cards = apply_pool_rules(
        [card],
        score_line=35.0,
        market_snapshot=snapshot,
        risk_template="balanced",
        agent_available=True,
    )
    params = {
        "score_line": 35.0,
        "risk_template": "balanced",
        "output_count": 3,
        "hide_recent_repeats": True,
    }
    pipeline = build_pipeline_audit(
        snapshot,
        params,
        total_contexts=2,
        prelim_count=1,
        skill_input_count=1,
        skill_review_limit=1,
        skill_review_concurrency=1,
        skill_demo_limited=False,
        skill_agent_call_count=1,
        skill_gate_audit=gate_result["audit"],
        prefilter_audit={
            "prefilter_candidate_count": 1,
            "rows": [{
                "code": "600001",
                "name": "黄金样本",
                "industry": "半导体",
                "price": 12.34,
                "pct": 3.2,
                "amount_yi": 8.6,
                "turnover_rate": 6.1,
                "passed": True,
                "common_pass": True,
                "reason": "技能预过滤命中",
                "skill_pass_ids": ["golden-momentum"],
            }],
        },
    )
    filter_audit = build_filter_audit(
        prefilter_audit=pipeline["prefilter"],
        skill_gate_audit=gate_result["audit"],
        scored_cards=[card],
        recommended_cards=recommended_cards,
        pipeline=pipeline,
    )
    recommendation = serialize_recommendation(
        SimpleNamespace(
            recommendation_id="rec-golden-001",
            run_id="run-golden-001",
            trade_date="2026-06-01",
            recommended_at=datetime(2026, 6, 1, 9, 30),
            code=card["code"],
            name=card["name"],
            industry=card["industry"],
            recommend_price=card["recommend_price"],
            bucket="golden-momentum",
            final_score=card["final_score"],
            consensus_score=card["consensus_score"],
            setup_type=card["setup_type"],
            entry_rule=card["entry_rule"],
            invalid_rule=card["invalid_rule"],
            rationale="黄金 fixture",
            risk_tags_json=json.dumps(card["risk_flags"]),
            info_tags_json="[]",
            feature_snapshot_json=json.dumps(card["feature_snapshot"]),
            market_context_json=json.dumps(snapshot),
            skill_breakdown_json=json.dumps(card["strategy_signals"]),
            agent_summary_json=json.dumps({"logs": card["agent_logs"], "stance": "support", "score_delta": 8.0}),
            status="pending",
            recent_repeat=False,
        ),
        watch=SimpleNamespace(watch_status="watching", note="黄金观察"),
        run=SimpleNamespace(preset_id="preset-golden", run_name="黄金运行"),
        preset_name="黄金预设",
    )
    run = serialize_run_summary(
        SimpleNamespace(
            run_id="run-golden-001",
            run_name="黄金运行",
            preset_id="preset-golden",
            trade_date="2026-06-01",
            run_mode="formal",
            started_at=datetime(2026, 6, 1, 9, 30),
            finished_at=datetime(2026, 6, 1, 9, 31),
            market_scope="all",
            market_scope_value=None,
            risk_template="balanced",
            score_line=35.0,
            universe_count=2,
            recommended_count=1,
            status="completed",
            error_message=None,
            heartbeat_at=datetime(2026, 6, 1, 9, 31),
            progress_phase="completed",
            progress_current=1,
            progress_total=1,
            progress_message="运行完成",
            interrupted_at=None,
            selected_skills_json='[{"skill_id":"golden-momentum"}]',
            result_summary_json='{"top_codes":["600001"]}',
            checkpoint_phase="persist",
        ),
        SimpleNamespace(name="黄金预设"),
        hit_rate=0.75,
        active_statuses={"queued", "running"},
        heartbeat_stale_seconds=1200,
        now=datetime(2026, 6, 1, 9, 32),
    )

    actual = _normalize(card, pipeline, filter_audit, recommendation, run)
    assert actual == {
        "candidate": {
            "code": "600001",
            "final_score": 90.11,
            "consensus_score": 88.6,
            "setup_type": "突破确认",
            "strategy_ids": ["golden-momentum"],
            "evidence": ["量能放大", "主线共振", "突破后承接稳定"],
            "risk_flags": ["情绪冰点，仅允许小仓试错主线龙头"],
            "effective_score_line": 35.0,
            "passed": True,
        },
        "pipeline": {
            "actual_scan_count": 1,
            "skill_input_count": 1,
            "skill_gate_candidate_count": 1,
            "skill_agent_call_count": 1,
            "effective_output_count": None,
        },
        "filter_audit": {
            "stage": "recommended",
            "status": "recommended",
            "reason": "达到正式分数线",
            "trace_stages": [
                "prefilter_common",
                "prefilter_pass",
                "skill_gate",
                "skill_review",
                "score_line",
            ],
            "reason_counts": {"recommended": {"达到正式分数线": 1}},
        },
        "recommendation_projection": {
            "recommendation_id": "rec-golden-001",
            "run_id": "run-golden-001",
            "code": "600001",
            "final_score": 90.11,
            "watch_status": "watching",
            "llm_stance": "support",
            "llm_delta": 8.0,
            "preset_id": "preset-golden",
            "run_name": "黄金运行",
        },
        "run_projection": {
            "run_id": "run-golden-001",
            "preset_id": "preset-golden",
            "status": "completed",
            "progress_pct": 100.0,
            "is_stale": False,
            "skill_count": 1,
            "hit_rate": 0.75,
        },
    }
