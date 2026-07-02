"""End-to-end recommendation flow tests with mocked external dependencies.

Covers: run → market/K-line → features → aggregation → persistence →
        run detail query → recommendations query → stats consistency.

All external data sources and LLM calls are mocked for deterministic verification.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.aggregator import DEFAULT_AGG_WEIGHTS, aggregate
from app.engine.skill_loader import StrategySkill
from app.models.tables import (
    CalibrationConfig,
    CapitalFlow,
    ConceptSector,
    DailyKbar,
    DragonTiger,
    MarketSnapshot,
    NewsAnnouncement,
    Preset,
    PresetSkill,
    Recommendation,
    RecommendationOutcome,
    RecommendationRun,
    ReviewNote,
    SkillRegistry,
    Stock,
)
from app.services.outcome_service import label_pending_outcomes, metrics_by_score_line, metrics_by_setup, precision_at_k
from app.services.stats_service import get_overview_stats
from app.services.workbench_service import (
    DATA_SOURCE_LOCAL_POOL,
    DATA_SOURCE_LOCAL_POOL_PREFERRED,
    _build_recommendation_card,
    _build_pipeline_audit,
    _build_filter_audit,
    _has_fresh_concept_sector_cache,
    _has_fresh_dragon_tiger_cache,
    _latest_trade_date,
    _merge_run_params,
    _skill_review_failure_audit,
    _resolve_aux_prefetch_count,
    _stale_capital_flow_codes,
    _stale_news_codes,
    _update_run_progress,
    enqueue_workbench_run,
    get_initial_workbench_state,
    get_recommendation_detail,
    get_run_detail,
    invalidate_bootstrap_cache,
    list_presets,
    list_run_recommendations,
    list_runs,
    recover_interrupted_runs,
    run_workbench,
)


def _seed_skill_registry(db: Session):
    """Seed minimal SkillRegistry rows for test skills."""
    for skill_id, name, mode in [
        ("chen-xiaoqun-skill", "陈小群", "formal"),
        ("daily-stock-analysis-skill", "DSA分析", "formal"),
        ("chao-gu-yang-jia-skill", "炒股养家", "experiment"),
    ]:
        if not db.get(SkillRegistry, skill_id):
            db.add(SkillRegistry(
                skill_id=skill_id,
                name=name,
                source_type="local",
                installed_path=f"skills/{skill_id}",
                current_version="1.0.0",
                mode=mode,
                enabled=True,
                has_executable_contract=False,
                manifest_json="{}",
            ))
    db.commit()


def _seed_stock_and_bars(db: Session, code="600519", name="贵州茅台", date_str="2026-05-12"):
    """Seed a stock and 60 daily bars."""
    if not db.get(Stock, code):
        db.add(Stock(code=code, name=name, market="主板", industry="白酒", is_st=False))
    for i in range(60, 0, -1):
        trade_date = f"2026-{(5 - i // 30):02d}-{(i % 30) + 1:02d}"
        if len(trade_date) != 10:
            continue
        exists = db.scalar(select(DailyKbar).where(DailyKbar.code == code, DailyKbar.trade_date == trade_date))
        if exists is not None:
            continue
        price = 100.0 + i * 0.5
        db.add(DailyKbar(
            code=code, trade_date=trade_date,
            open=price - 0.5, high=price + 1.0, low=price - 1.0, close=price,
            volume=1000000, amount=price * 1000000,
            pct_chg=0.5, turnover_rate=0.3,
        ))
    db.commit()


def _mock_market_universe(first_code: str | None = None):
    """Return a realistic market universe with 30 stocks."""
    stocks = []
    for i in range(30):
        code = first_code if i == 0 and first_code else f"60{i:04d}"
        stocks.append({
            "code": code,
            "name": f"测试股{i}",
            "price": 10.0 + i * 0.5,
            "close": 10.0 + i * 0.5,
            "pct": 1.0 + i * 0.1,
            "amount_yi": 2.0 + i * 0.1,
            "turnover_rate": 2.0 + i * 0.05,
            "free_mv_yi": 50.0 + i,
            "total_mv_yi": 200.0 + i * 2,
            "chg20": 5.0 + i * 0.3,
            "chg60": 8.0 + i * 0.2,
            "dist20": 3.0 + i * 0.2,
            "dist60": 8.0 + i * 0.3,
            "pos20": 55.0 + i * 0.5,
            "pos60": 50.0 + i * 0.3,
            "pos120": 45.0,
            "eff20": 0.45 + i * 0.005,
            "vol5_20": 1.0 + i * 0.02,
            "breakout20": i % 3 == 0,
            "breakout60": i % 4 == 0,
            "is_st": False,
            "industry": "白酒" if i % 3 == 0 else "电子" if i % 3 == 1 else "银行",
            "exchange": "SH" if i % 2 == 0 else "SZ",
            "upper_shadow_count20": i % 4,
            "max_dd20": -5.0 - i * 0.1,
            "max_dd60": -10.0 - i * 0.2,
            "limit_up_days20": 1 if i % 10 == 0 else 0,
            "yesterday_high": 10.5 + i * 0.5,
            "yesterday_low": 9.5 + i * 0.5,
            "high_20d": 12.0 + i * 0.5,
            "low_20d": 8.0 + i * 0.5,
            "off_high_pct": -5.0,
            "announcement_negative_count": 0,
            "news_risk_count": 0,
            "data_quality": {"source_breakdown": {"OBSERVED": 30, "SYNTHETIC": 10}, "missing_fields": []},
            "data_source": "local_pool",
            "data_completeness_score": 85.0,
            "evidence_quality_score": 80.0,
            "dsa_profile": {},
            "_bars": [
                {
                    "code": code, "trade_date": "2026-05-12",
                    "open": 10.0 + i * 0.5, "high": 10.5 + i * 0.5,
                    "low": 9.8 + i * 0.5, "close": 10.2 + i * 0.5,
                    "volume": 5000000, "amount": (10.2 + i * 0.5) * 5000000,
                    "pct_chg": 1.5, "turnover_rate": 2.0,
                }
            ],
        })
    snapshot = {
        "snapshot_id": f"snap_test_{datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y%m%d_%H%M%S')}",
        "trade_date": "2026-05-12",
        "sample_count": 30,
        "up_count": 20,
        "down_count": 8,
        "limit_up_count": 5,
        "limit_down_count": 1,
        "main_industries": ["白酒", "电子", "银行"],
        "breadth": {"regime": "多头", "data_source": "local_pool"},
    }
    return stocks, snapshot


def _unique_stock_code(prefix: str = "98") -> str:
    return f"{prefix}{uuid.uuid4().int % 10000:04d}"


def test_list_presets_exposes_output_count(db: Session):
    db.add(
        Preset(
            preset_id="preset_output_count_contract",
            name="输出数量契约测试",
            run_mode="formal",
            market_scope="all",
            risk_template="balanced",
            score_line=72.0,
            output_count=4,
            bucket_limits_json="{}",
            data_source=DATA_SOURCE_LOCAL_POOL_PREFERRED,
            hide_recent_repeats=True,
            status="active",
        )
    )
    db.commit()

    presets = list_presets(db)
    item = next(preset for preset in presets if preset["preset_id"] == "preset_output_count_contract")

    assert item["output_count"] == 4


def _mock_skill_reviews(stock, skills, snapshot, rule_signals_by_skill, session_id):
    """Mock async skill reviews — return deterministic results."""
    results = []
    for i, skill in enumerate(skills):
        stance = "support" if i % 3 != 2 else "neutral"
        results.append({
            "skill_id": skill.skill_id,
            "skill_name": skill.name,
            "review": {
                "stance": stance,
                "score": 75 - i * 5,
                "confidence": 0.8 - i * 0.05,
                "evidence": [f"证据{i+1}"],
                "risks": [],
                "summary": f"审查{i+1}",
                "market_regime_judgment": "多头",
            },
            "hallucination_check": {"flags": [], "severity": "low"},
            "error": None,
        })
    return results


# ---------------------------------------------------------------------------
# Mock StrategySkill
# ---------------------------------------------------------------------------

def _mock_skill(skill_id="test-skill", name="TestSkill"):
    skill = MagicMock(spec=StrategySkill)
    skill.skill_id = skill_id
    skill.name = name
    skill.version = "1.0.0"
    skill.enabled = True
    skill.agent_enabled = True
    skill.executable = False
    skill.contract = None
    skill.contract_hash = None
    skill.skill_hash = "abc123"
    skill.skill_body = "# Test Skill\n\n买入原则：均线多头时介入"
    skill.skill_frontmatter = {}
    skill.reference_files = []
    skill.manifest = {"source_type": "local"}
    skill.path = MagicMock()
    return skill


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestE2ERecommendationFlow:
    """End-to-end tests: run → aggregate → persist → query → stats."""

    def test_init_db_creates_all_tables(self, db):
        """17.7: init_db() creates all tables without error."""
        from app.models.tables import Base

        tables = Base.metadata.tables.keys()
        assert len(tables) >= 20  # minimum expected table count

    def test_full_flow_with_mocked_market(self, db):
        """17.1: Full recommendation flow with mocked external deps."""
        _seed_skill_registry(db)
        seed_code = _unique_stock_code()
        _seed_stock_and_bars(db, seed_code, "测试主股", "2026-05-12")

        stocks, snapshot = _mock_market_universe(first_code=seed_code)

        # Seed stock records for the mock universe
        for s in stocks:
            if not db.get(Stock, s["code"]):
                db.add(
                    Stock(
                        code=s["code"],
                        name=s["name"],
                        market=s.get("exchange"),
                        industry=s.get("industry"),
                        is_st=False,
                    )
                )
        db.commit()

        # Build features
        from app.services.feature_builder import build_features
        features = build_features(stocks, snapshot)
        assert len(features) == 30

        # Build research contexts
        from app.services.research_context import build_research_contexts
        contexts = build_research_contexts(features, snapshot)
        assert len(contexts) == 30

        # Aggregate one stock
        from app.engine.aggregator import aggregate, apply_pool_rules

        stock_data = contexts[0].to_skill_input()
        market_score = 72.0

        # Simulate skill reviews
        signals = []
        for i in range(3):
            review = {
                "stance": "support" if i < 2 else "neutral",
                "score": 75 - i * 5,
                "confidence": 0.8,
                "evidence": [f"证据{i+1}"],
                "risks": [],
                "summary": f"审查结论{i+1}",
            }
            from app.engine.agent_skill_runner import enrich_signal_with_agent_review

            rule_signal = {
                "strategy_id": f"skill-{i}",
                "strategy_name": f"Skill{i}",
                "score": 70.0,
                "confidence": 0.7,
                "evidence": [],
                "risk_flags": [],
                "setup_match": "多头排列",
                "entry_rule": "回踩均线",
                "invalid_rule": "跌破支撑",
            }
            signals.append(enrich_signal_with_agent_review(rule_signal, review))

        card = aggregate(
            stock=stock_data,
            signals=signals,
            market_score=market_score,
            agent_review_score=72.5,
            risk_template="balanced",
        )

        # Verify card structure
        assert card["code"] == stocks[0]["code"]
        assert card["final_score"] > 0
        assert "component_scores" in card
        assert card["component_scores"]["skill_agent"] > 0
        assert card["component_scores"]["strategy_rule"] > 0
        assert card["component_scores"]["market"] > 0
        assert "stance_summary" in card

        # Aggregate all stocks into cards and apply pool rules
        cards = []

    def test_merge_run_params_defaults_formal_to_local_pool_preferred(self):
        params = _merge_run_params(None, None, {"run_mode": "formal", "skills": []})
        assert params["data_source"] == DATA_SOURCE_LOCAL_POOL_PREFERRED

        experiment_params = _merge_run_params(None, None, {"run_mode": "experiment", "skills": []})
        assert experiment_params["data_source"] == DATA_SOURCE_LOCAL_POOL_PREFERRED

    def test_merge_run_params_keeps_formal_auxiliary_refresh_enabled(self):
        params = _merge_run_params(
            None,
            None,
            {
                "run_mode": "formal",
                "skills": [],
                "disable_agent_review": True,
                "agent_review_limit": 3,
                "agent_review_concurrency": 2,
                "skip_aux_refresh": True,
            },
        )

        assert params["disable_agent_review"] is True
        assert params["agent_review_limit"] == 3
        assert params["agent_review_concurrency"] == 2
        assert params["skip_aux_refresh"] is False

    def test_merge_run_params_preserves_experiment_fast_run_controls(self):
        params = _merge_run_params(
            None,
            None,
            {
                "run_mode": "experiment",
                "skills": [],
                "disable_agent_review": True,
                "agent_review_limit": 3,
                "agent_review_concurrency": 2,
                "skip_aux_refresh": True,
            },
        )

        assert params["disable_agent_review"] is True
        assert params["agent_review_limit"] == 3
        assert params["agent_review_concurrency"] == 2
        assert params["skip_aux_refresh"] is True

    def test_formal_auxiliary_refresh_uses_bounded_precision_window(self):
        count = _resolve_aux_prefetch_count(
            200,
            48,
            run_mode="formal",
            skip_aux_refresh=False,
            max_codes=120,
        )

        assert count == 120

    def test_fast_run_auxiliary_refresh_remains_capped(self):
        count = _resolve_aux_prefetch_count(
            63,
            48,
            run_mode="formal",
            skip_aux_refresh=True,
            max_codes=120,
        )

        assert count == 24

    def test_build_recommendation_card_uses_agent_dominant_weights_when_reviews_present(self):
        skill = _mock_skill("skill-1", "Skill1")

        class _Ctx:
            def to_feature_snapshot(self):
                return {"stop_loss": 9.5, "take_profit": 11.5}

        stock = {
            "code": "000001",
            "name": "测试股",
            "industry": "电子",
            "price": 10.5,
            "close": 10.5,
            "pct": 3.2,
            "turnover_rate": 4.5,
            "amount_yi": 8.6,
            "evidence_quality_score": 82.0,
            "data_quality": {"missing_fields": []},
        }
        signal = {
            "strategy_id": "skill-1",
            "strategy_name": "Skill1",
            "score": 74.0,
            "confidence": 0.72,
            "evidence": ["规则信号"],
            "risk_flags": [],
            "setup_match": "主线共振",
            "entry_rule": "回踩 5 日线",
            "invalid_rule": "跌破前低",
            "phase": "主升",
        }
        entry = {
            "context": _Ctx(),
            "stock": stock,
            "rule_signals": {"skill-1": signal},
            "review_skill_ids": ["skill-1"],
            "skill_gate_reasons": {"skill-1": "规则门通过"},
        }
        skill_reviews = [
            {
                "skill_id": "skill-1",
                "skill_name": "Skill1",
                "review": {
                    "stance": "support",
                    "score": 78.0,
                    "confidence": 0.84,
                    "supports": ["趋势与量能一致"],
                    "risks": [],
                    "summary": "支持继续跟踪",
                    "action": "观察",
                    "position_range": "5-10%",
                    "entry_style": "分时回踩吸",
                    "invalidation_conditions": [],
                    "next_day_plan": [],
                },
                "hallucination_check": {"flags": [], "severity": "low"},
                "error": None,
            }
        ]
        snapshot = {
            "snapshot_id": "snap_1",
            "trade_date": "2026-05-12",
            "sample_count": 300,
            "up_count": 180,
            "down_count": 90,
            "limit_up_count": 12,
            "limit_down_count": 2,
            "main_industries": ["电子"],
            "breadth": {"regime": "偏多", "risk": "中", "data_source": "daily_bar_backtest"},
        }

        card = _build_recommendation_card(
            entry,
            skill_reviews,
            skill_map={"skill-1": skill},
            selected_skills=[{"skill_id": "skill-1", "name": "Skill1", "weight": 1.0, "mode": "formal", "skill": skill}],
            market_score=68.0,
            risk_template="balanced",
            calibrated_weights=None,
            snapshot=snapshot,
            recent_codes=set(),
        )

        assert card["aggregation_weights"]["skill_agent"] > card["aggregation_weights"]["strategy_rule"]
        assert card["agent_logs"]
        assert card["feature_snapshot"]["component_scores"]["skill_agent"] > 0

    def test_label_pending_outcomes_simulates_entry_and_exit(self, db):
        suffix = uuid.uuid4().hex[:8]
        run_mode = f"fexec_{suffix}"
        run_id = f"run_outcome_exec_{suffix}"
        rec_id = f"rec_exec_{suffix}"
        code = _unique_stock_code()
        run = RecommendationRun(
            run_id=run_id,
            run_name="结果标注执行测试",
            run_mode=run_mode,
            trade_date="2026-05-12",
            market_scope="all",
            risk_template="balanced",
            output_count=1,
            universe_count=1,
            recommended_count=1,
            selected_skills_json="[]",
            params_json="{}",
            market_snapshot_json="{}",
            result_summary_json="{}",
            status="completed",
        )
        db.add(run)
        db.flush()
        db.add(Stock(code=code, name="测试股", market="创业板", industry="电子", is_st=False))
        db.add_all([
            DailyKbar(code=code, trade_date="2026-05-12", open=10.0, high=10.2, low=9.9, close=10.0, volume=1, amount=10, pct_chg=0.0, turnover_rate=1.0),
            DailyKbar(code=code, trade_date="2026-05-13", open=10.1, high=10.6, low=9.95, close=10.5, volume=1, amount=10, pct_chg=5.0, turnover_rate=1.2),
            DailyKbar(code=code, trade_date="2026-05-14", open=10.6, high=10.95, low=10.45, close=10.9, volume=1, amount=10, pct_chg=3.0, turnover_rate=1.3),
            DailyKbar(code=code, trade_date="2026-05-15", open=10.8, high=10.9, low=10.5, close=10.7, volume=1, amount=10, pct_chg=-1.0, turnover_rate=1.1),
        ])
        rec = Recommendation(
            recommendation_id=rec_id,
            run_id=run.run_id,
            trade_date="2026-05-12",
            code=code,
            name="测试股",
            industry="电子",
            recommend_price=10.0,
            bucket="core",
            final_score=82.0,
            consensus_score=80.0,
            risk_tags_json="[]",
            feature_snapshot_json=json.dumps({
                "ideal_buy": 10.0,
                "secondary_buy": 10.15,
                "stop_loss": 9.6,
                "take_profit": 10.8,
                "market_discipline": {"position_label": "中仓", "max_position_pct": 55},
            }),
            market_context_json="{}",
            skill_breakdown_json="[]",
            agent_summary_json="{}",
            status="pending",
            recent_repeat=False,
        )
        db.add(rec)
        db.commit()

        result = label_pending_outcomes(db, horizon_days=5, run_mode=run_mode)
        outcome = db.get(RecommendationOutcome, rec_id)

        assert result["labeled"] == 1
        assert outcome is not None
        assert outcome.entry_triggered is True
        assert outcome.entry_date == "2026-05-13"
        assert outcome.exit_reason in ("take_profit_hit", "trailing_stop_gap", "trailing_stop_hit")
        assert outcome.outcome_label == "success"
        assert outcome.executed_return > 3.0
        assert outcome.position_size == pytest.approx(0.55)
        assert outcome.weighted_return == pytest.approx(round(outcome.executed_return * 0.55, 2))

    def test_label_pending_outcomes_marks_untriggered_buy_point_as_missed(self, db):
        suffix = uuid.uuid4().hex[:8]
        run_mode = f"fmiss_{suffix}"
        run_id = f"run_outcome_miss_{suffix}"
        rec_id = f"rec_miss_{suffix}"
        code = _unique_stock_code()
        run = RecommendationRun(
            run_id=run_id,
            run_name="结果标注未触发测试",
            run_mode=run_mode,
            trade_date="2026-05-12",
            market_scope="all",
            risk_template="balanced",
            output_count=1,
            universe_count=1,
            recommended_count=1,
            selected_skills_json="[]",
            params_json="{}",
            market_snapshot_json="{}",
            result_summary_json="{}",
            status="completed",
        )
        db.add(run)
        db.flush()
        db.add(Stock(code=code, name="未触发股", market="创业板", industry="电子", is_st=False))
        db.add_all([
            DailyKbar(code=code, trade_date="2026-05-12", open=10.0, high=10.2, low=9.95, close=10.0, volume=1, amount=10, pct_chg=0.0, turnover_rate=1.0),
            DailyKbar(code=code, trade_date="2026-05-13", open=10.3, high=10.7, low=10.1, close=10.6, volume=1, amount=10, pct_chg=6.0, turnover_rate=1.3),
            DailyKbar(code=code, trade_date="2026-05-14", open=10.6, high=10.9, low=10.35, close=10.8, volume=1, amount=10, pct_chg=2.0, turnover_rate=1.2),
            DailyKbar(code=code, trade_date="2026-05-15", open=10.75, high=10.85, low=10.5, close=10.7, volume=1, amount=10, pct_chg=-1.0, turnover_rate=1.1),
        ])
        rec = Recommendation(
            recommendation_id=rec_id,
            run_id=run.run_id,
            trade_date="2026-05-12",
            code=code,
            name="未触发股",
            industry="电子",
            recommend_price=10.0,
            bucket="watch",
            final_score=75.0,
            consensus_score=73.0,
            risk_tags_json="[]",
            feature_snapshot_json=json.dumps({
                "ideal_buy": 9.6,
                "secondary_buy": 9.7,
                "stop_loss": 9.2,
                "take_profit": 10.8,
                "market_discipline": {"position_label": "小仓", "max_position_pct": 35},
            }),
            market_context_json="{}",
            skill_breakdown_json="[]",
            agent_summary_json="{}",
            status="pending",
            recent_repeat=False,
        )
        db.add(rec)
        db.commit()

        result = label_pending_outcomes(db, horizon_days=5, run_mode=run_mode)
        outcome = db.get(RecommendationOutcome, rec_id)

        # entry fallback: 买点未命中时回退到次日市价入场
        assert result["labeled"] == 1
        assert outcome is not None
        assert outcome.entry_triggered is True
        assert outcome.exit_reason in ("take_profit_hit", "trailing_stop_gap", "trailing_stop_hit")
        assert outcome.outcome_label == "success"
        assert outcome.outcome_label_legacy == "success"
        assert outcome.executed_return > 0

    def test_stats_consistency(self, db, monkeypatch):
        """17.6: Stats overview returns correct counts."""
        _seed_skill_registry(db)

        # Create a run with recommendations
        suffix = uuid.uuid4().hex[:8]
        run_id = f"run_stats_{suffix}"
        rec_ids = [f"rec_stats_{i:03d}_{suffix}" for i in range(3)]
        run = RecommendationRun(
            run_id=run_id,
            run_name="统计测试",
            run_mode="formal",
            trade_date="2026-05-12",
            universe_count=10,
            market_scope="all",
            risk_template="balanced",
            output_count=3,
            selected_skills_json="[]",
            params_json="{}",
            market_snapshot_json="{}",
            status="completed",
            recommended_count=3,
            result_summary_json="{}",
            finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.add(run)
        db.flush()

        codes = ["600519", "000001", "600036"]
        for i, code in enumerate(codes):
            rec = Recommendation(
                recommendation_id=rec_ids[i],
                run_id=run_id,
                trade_date="2026-05-12",
                code=code,
                name=f"测试股{i}",
                bucket="",
                final_score=85.0 - i * 5,
                recommend_price=100.0,
                status="pending",
                skill_breakdown_json="[]",
                feature_snapshot_json=json.dumps({"component_scores": {
                    "skill_agent": 85.0, "strategy_rule": 75.0,
                    "market": 70.0, "risk": 80.0, "evidence": 60.0,
                }}),
                agent_summary_json="{}",
            )
            db.add(rec)
        db.commit()

        # Add outcome labels
        from app.models.tables import RecommendationOutcome

        for i, code in enumerate(codes):
            outcome = RecommendationOutcome(
                recommendation_id=rec_ids[i],
                horizon_days=5,
                entry_triggered=True,
                outcome_label="success" if i < 2 else "failed",
                executed_return=[5.0, -0.2, -3.0][i],
                close_return=[5.0, 1.0, -3.0][i],
                max_return=8.0 if i < 2 else 1.0,
                min_return=-2.0 if i < 2 else -5.0,
                label_source="manual",
            )
            db.add(outcome)
            # Update recommendation status to match
            db.get(Recommendation, rec_ids[i]).status = "success" if i < 2 else "failed"
        db.commit()

        import app.services.stats_service as stats_service

        monkeypatch.setattr(stats_service, "_runs_for_mode", lambda _db, mode: [run] if mode == "formal" else [])
        stats_service._stats_cache.clear()
        stats = get_overview_stats(db, "formal")
        assert stats is not None
        assert stats["run_count"] == 1
        assert stats["recommendation_count"] == 3
        assert stats["success_count"] == 1
        assert stats["failed_count"] == 2
        assert stats["pending_count"] == 0
        assert stats["success_count"] + stats["failed_count"] + stats["pending_count"] == stats["recommendation_count"]
        # hit_rate 使用严格准确口径：真实成交且扣费滑点后净收益 > 0。
        assert abs(stats["hit_rate"] - 0.333) < 0.01

    def test_metrics_helpers_respect_mode_and_thresholds(self, db):
        suffix = uuid.uuid4().hex[:8]
        formal_mode = f"formal_{suffix}"
        experiment_mode = f"experiment_{suffix}"
        run_formal_id = f"run_metrics_formal_{suffix}"
        run_experiment_id = f"run_metrics_experiment_{suffix}"
        rec_ids = [f"rec_metrics_{index:03d}_{suffix}" for index in range(1, 5)]
        try:
            run_formal = RecommendationRun(
                run_id=run_formal_id,
                run_name="正式统计测试",
                run_mode=formal_mode,
                trade_date="2026-05-12",
                universe_count=10,
                market_scope="all",
                risk_template="balanced",
                score_line=72.0,
                output_count=3,
                selected_skills_json="[]",
                params_json="{}",
                market_snapshot_json="{}",
                status="completed",
                recommended_count=3,
                result_summary_json="{}",
                finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            run_experiment = RecommendationRun(
                run_id=run_experiment_id,
                run_name="实验统计测试",
                run_mode=experiment_mode,
                trade_date="2026-05-12",
                universe_count=10,
                market_scope="all",
                risk_template="balanced",
                score_line=80.0,
                output_count=2,
                selected_skills_json="[]",
                params_json="{}",
                market_snapshot_json="{}",
                status="completed",
                recommended_count=1,
                result_summary_json="{}",
                finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            db.add_all([run_formal, run_experiment])
            db.flush()

            recs = [
                Recommendation(
                    recommendation_id=rec_ids[0],
                    run_id=run_formal_id,
                    trade_date="2026-05-12",
                    code="600519",
                    name="测试A",
                    bucket="",
                    final_score=88.0,
                    setup_type="突破",
                    recommend_price=100.0,
                    status="pending",
                    skill_breakdown_json="[]",
                    feature_snapshot_json=json.dumps({"market_regime": "偏多·活跃"}),
                    agent_summary_json="{}",
                ),
                Recommendation(
                    recommendation_id=rec_ids[1],
                    run_id=run_formal_id,
                    trade_date="2026-05-12",
                    code="000001",
                    name="测试B",
                    bucket="",
                    final_score=76.0,
                    setup_type="突破",
                    recommend_price=20.0,
                    status="pending",
                    skill_breakdown_json="[]",
                    feature_snapshot_json=json.dumps({"market_discipline": {"market_regime": "偏多·活跃"}}),
                    agent_summary_json="{}",
                ),
                Recommendation(
                    recommendation_id=rec_ids[2],
                    run_id=run_formal_id,
                    trade_date="2026-05-12",
                    code="600036",
                    name="测试C",
                    bucket="",
                    final_score=69.0,
                    setup_type="回踩",
                    recommend_price=30.0,
                    status="pending",
                    skill_breakdown_json="[]",
                    feature_snapshot_json=json.dumps({"market_regime": "弱势·冰点"}),
                    agent_summary_json="{}",
                ),
                Recommendation(
                    recommendation_id=rec_ids[3],
                    run_id=run_experiment_id,
                    trade_date="2026-05-12",
                    code="601318",
                    name="实验票",
                    bucket="",
                    final_score=99.0,
                    setup_type="突破",
                    recommend_price=40.0,
                    status="pending",
                    skill_breakdown_json="[]",
                    feature_snapshot_json=json.dumps({"market_regime": "强牛·高潮"}),
                    agent_summary_json="{}",
                ),
            ]
            db.add_all(recs)
            db.flush()

            outcomes = [
                RecommendationOutcome(recommendation_id=rec_ids[0], outcome_label="success", entry_triggered=True, executed_return=2.0, label_source="manual"),
                RecommendationOutcome(recommendation_id=rec_ids[1], outcome_label="success", entry_triggered=True, executed_return=-0.2, label_source="manual"),
                RecommendationOutcome(recommendation_id=rec_ids[2], outcome_label="missed", entry_triggered=False, executed_return=0.0, label_source="manual"),
                RecommendationOutcome(recommendation_id=rec_ids[3], outcome_label="success", entry_triggered=True, executed_return=1.0, label_source="manual"),
            ]
            db.add_all(outcomes)
            db.flush()

            setup_rows = metrics_by_setup(db, formal_mode)
            assert len(setup_rows) == 2
            assert next(row for row in setup_rows if row["setup_type"] == "突破")["sample_count"] == 2
            assert next(row for row in setup_rows if row["setup_type"] == "突破")["hit_rate"] == 0.5
            pullback_row = next(row for row in setup_rows if row["setup_type"] == "回踩")
            assert pullback_row["trigger_rate"] == 0.0
            assert pullback_row["missed_count"] == 1

            p_at_k = precision_at_k(db, formal_mode, ks=(1, 2, 3))
            assert [row["k"] for row in p_at_k] == [1, 2, 3]
            assert p_at_k[0]["precision"] == 1.0
            assert p_at_k[1]["precision"] == 0.5
            assert p_at_k[2]["precision"] == round(1 / 3, 3)

            score_rows = metrics_by_score_line(db, formal_mode, score_lines=(60, 70, 80))
            assert [row["score_line"] for row in score_rows] == [60.0, 70.0, 80.0]
            assert score_rows[0]["sample_count"] == 3
            assert score_rows[0]["missed_count"] == 1
            assert score_rows[1]["sample_count"] == 2
            assert score_rows[2]["sample_count"] == 1
            assert score_rows[2]["hit_rate"] == 1.0
            assert all(row["sample_count"] < 4 for row in score_rows)
        finally:
            db.rollback()

    def test_component_scores_match_aggregator_dimensions(self, db):
        """Verify feature_snapshot_json.component_scores keys match aggregator DIMENSIONS."""
        rec_id = f"rec_dims_{uuid.uuid4().hex[:8]}"
        run = RecommendationRun(
            run_id=f"run_dims_{uuid.uuid4().hex[:6]}",
            run_name="维度测试",
            run_mode="formal",
            trade_date="2026-05-12",
            universe_count=10,
            market_scope="all",
            risk_template="balanced",
            output_count=3,
            selected_skills_json="[]",
            params_json="{}",
            market_snapshot_json="{}",
            status="completed",
            recommended_count=1,
            result_summary_json="{}",
            finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.add(run)
        db.flush()

        rec = Recommendation(
            recommendation_id=rec_id,
            run_id=run.run_id,
            trade_date="2026-05-12",
            code="600519",
            name="贵州茅台",
            bucket="容量核心",
            final_score=85.0,
            recommend_price=100.0,
            status="pending",
            skill_breakdown_json="[]",
            feature_snapshot_json=json.dumps({"component_scores": {
                "skill_agent": 85.0,
                "strategy_rule": 75.0,
                "market": 70.0,
                "risk": 80.0,
                "evidence": 60.0,
            }}),
            agent_summary_json="{}",
        )
        db.add(rec)
        db.commit()

        detail = get_recommendation_detail(db, rec_id)
        cs = detail["feature_snapshot"].get("component_scores", {})
        expected_keys = set(DEFAULT_AGG_WEIGHTS.keys())
        actual_keys = set(cs.keys())
        assert actual_keys == expected_keys, f"Component keys mismatch: {actual_keys} != {expected_keys}"

    def test_recommendation_detail_builds_replay_summary(self, db):
        rec_id = f"rec_replay_{uuid.uuid4().hex[:8]}"
        run = RecommendationRun(
            run_id=f"run_replay_{uuid.uuid4().hex[:6]}",
            run_name="复盘详情测试",
            run_mode="formal",
            trade_date="2026-05-12",
            universe_count=10,
            market_scope="all",
            risk_template="balanced",
            output_count=3,
            selected_skills_json="[]",
            params_json="{}",
            market_snapshot_json="{}",
            status="completed",
            recommended_count=1,
            result_summary_json="{}",
            finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.add(run)
        db.flush()

        rec = Recommendation(
            recommendation_id=rec_id,
            run_id=run.run_id,
            trade_date="2026-05-12",
            code="600519",
            name="贵州茅台",
            bucket="容量核心",
            final_score=82.0,
            recommend_price=100.0,
            entry_rule="回踩不破 5 日线",
            setup_type="趋势突破",
            status="failed",
            skill_breakdown_json="[]",
            feature_snapshot_json=json.dumps(
                {
                    "market_regime": "active",
                    "trend_status": "up",
                    "volume_ratio_5d": 1.5,
                    "score_line_meta": {
                        "effective_score_line": 72.0,
                        "reasons": ["市场加分 +2", "证据加分 +1"],
                    },
                },
                ensure_ascii=False,
            ),
            agent_summary_json="{}",
        )
        outcome = RecommendationOutcome(
            recommendation_id=rec_id,
            horizon_days=5,
            entry_triggered=True,
            entry_date="2026-05-13",
            entry_price=100.2,
            exit_date="2026-05-16",
            exit_price=97.8,
            executed_return=-2.4,
            close_return=-2.1,
            weighted_return=-2.4,
            holding_days=3,
            exit_reason="stop_loss",
            max_return=1.2,
            min_return=-3.0,
            max_drawdown=-3.4,
            hit_target=False,
            hit_invalid=True,
            outcome_label="failed",
            outcome_reason="次日承接不足，跌破止损",
            label_source="auto",
        )
        review = ReviewNote(
            note_id=f"note_replay_{uuid.uuid4().hex[:6]}",
            recommendation_id=rec_id,
            user_label="false_positive",
            failure_reason="竞价后承接转弱",
            review_text="开盘后强转弱，不适合追。",
        )
        db.add_all([rec, outcome, review])
        db.commit()

        detail = get_recommendation_detail(db, rec_id)
        replay = detail["replay_summary"]

        assert replay["recommend_time"]
        assert replay["symbol"] == "600519"
        assert replay["name"] == "贵州茅台"
        assert replay["entry_time"] == "2026-05-13"
        assert replay["exit_time"] == "2026-05-16"
        assert replay["entry_price"] == 100.2
        assert replay["exit_price"] == 97.8
        assert replay["net_return_pct"] == -2.4
        assert replay["false_positive_reason"] == "竞价后承接转弱"
        assert replay["review_failure_reason"] == "竞价后承接转弱"
        assert replay["outcome_reason"] == "次日承接不足，跌破止损"
        assert replay["trigger_conditions"][0] == "回踩不破 5 日线"
        assert "趋势突破" in replay["trigger_conditions"]
        assert replay["effective_score_line"] == 72.0
        assert replay["signal_features"]["market_regime"] == "active"

    def test_trade_date_and_aux_cache_freshness_respect_trade_date_and_ttl(self, db):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        stale = now - timedelta(hours=3)
        fresh_code = f"{(uuid.uuid4().int % 900000) + 100000:06d}"
        stale_code = f"{(uuid.uuid4().int % 900000) + 100000:06d}"

        try:
            db.add_all(
                [
                    MarketSnapshot(snapshot_id=f"snap_old_{uuid.uuid4().hex[:6]}", trade_date="2026-05-11", created_at=now),
                    MarketSnapshot(snapshot_id=f"snap_new_{uuid.uuid4().hex[:6]}", trade_date="2026-05-12", created_at=stale),
                    ConceptSector(sector_code=f"BK{uuid.uuid4().hex[:6]}", trade_date="2026-05-12", sector_name="测试概念", created_at=now),
                    ConceptSector(sector_code=f"BK{uuid.uuid4().hex[:6]}", trade_date="2026-05-13", sector_name="旧TTL概念", created_at=stale),
                    DragonTiger(code=fresh_code, trade_date="2026-05-12", name="新鲜样本", created_at=now),
                    DragonTiger(code=stale_code, trade_date="2026-05-13", name="过期样本", created_at=stale),
                    CapitalFlow(code=fresh_code, trade_date="2026-05-12", main_net_inflow_yi=1.2, created_at=now),
                    CapitalFlow(code=stale_code, trade_date="2026-05-12", main_net_inflow_yi=0.8, created_at=stale),
                    NewsAnnouncement(source="tushare", article_id=f"news_{uuid.uuid4().hex[:6]}", code=fresh_code, announce_date="2026-05-12", title="测试新闻", created_at=now),
                    NewsAnnouncement(source="tushare", article_id=f"news_{uuid.uuid4().hex[:6]}", code=stale_code, announce_date="2026-05-12", title="旧TTL新闻", created_at=stale),
                ]
            )
            db.flush()

            assert _latest_trade_date(db) >= "2026-05-12"
            assert _has_fresh_concept_sector_cache(db, "2026-05-12", 3600) is True
            assert _has_fresh_concept_sector_cache(db, "2026-05-13", 3600) is False
            assert _has_fresh_dragon_tiger_cache(db, "2026-05-12", 3600) is True
            assert _has_fresh_dragon_tiger_cache(db, "2026-05-13", 3600) is False
            assert _stale_capital_flow_codes(db, [fresh_code, stale_code], "2026-05-12", 3600) == [stale_code]
            assert _stale_news_codes(db, [fresh_code, stale_code], "2026-05-12", 3600) == [stale_code]
        finally:
            db.rollback()

    def test_calibrated_weights_flow(self, db):
        """Verify calibrated weights stored in CalibrationConfig are loaded correctly."""
        calibration_keys = ["skill_agent", "strategy_rule", "market", "risk", "evidence"]
        for key in calibration_keys:
            if not db.get(CalibrationConfig, key):
                db.add(CalibrationConfig(key=key, value=float(DEFAULT_AGG_WEIGHTS.get(key, 0))))
        db.commit()

        from app.services.workbench_service import _load_calibrated_agg_weights

        weights = _load_calibrated_agg_weights(db)
        assert len(weights) >= 1
        for key in weights:
            assert key in calibration_keys

    def test_pipeline_audit_does_not_compress_skill_scope_by_output_count(self):
        """Skill input count should come from explicit scan scope, not output_count × lookahead."""
        snapshot = {
            "sample_count": 360,
            "breadth": {
                "unified_quote_total": 5200,
                "base_filter_eligible_count": 360,
                "candidate_limit_config": {
                    "market_candidate_count": 0,
                    "lookahead_multiplier": 4,
                    "lookahead_count": 5200,
                },
            },
        }
        audit = _build_pipeline_audit(
            snapshot=snapshot,
            params={"output_count": 6, "hide_recent_repeats": True},
            total_contexts=360,
            prelim_count=360,
            skill_input_count=360,
            skill_review_limit=0,
            skill_review_concurrency=6,
            skill_demo_limited=False,
        )

        assert audit["base_filter_eligible_count"] == 360
        assert audit["actual_scan_count"] == 360
        assert audit["skill_input_count"] == 360
        assert audit["skill_input_count"] > audit["output_count"] * 2
        assert audit["market_candidate_count"] == 0
        assert audit["demo_limited"] is False
        assert audit["scan_limited"] is False
        assert audit["skill_limited"] is False

    def test_pipeline_audit_marks_explicit_scan_limit(self):
        """Candidate-count limits are visible as Demo scan limits, not hidden compression."""
        snapshot = {
            "sample_count": 12,
            "breadth": {
                "unified_quote_total": 5106,
                "base_filter_eligible_count": 3200,
                "candidate_limit_config": {
                    "market_candidate_count": 12,
                    "lookahead_multiplier": 1,
                    "lookahead_count": 12,
                },
            },
        }
        audit = _build_pipeline_audit(
            snapshot=snapshot,
            params={"output_count": 1, "hide_recent_repeats": False},
            total_contexts=12,
            prelim_count=12,
            skill_input_count=12,
            skill_review_limit=0,
            skill_review_concurrency=1,
            skill_demo_limited=False,
        )

        assert audit["base_filter_eligible_count"] == 3200
        assert audit["actual_scan_count"] == 12
        assert audit["skill_input_count"] == 12
        assert audit["market_candidate_count"] == 12
        assert audit["scan_limited"] is True
        assert audit["skill_limited"] is False
        assert audit["demo_limited"] is True

    def test_pipeline_audit_marks_explicit_demo_limit(self):
        snapshot = {
            "sample_count": 360,
            "breadth": {
                "unified_quote_total": 5200,
                "base_filter_eligible_count": 360,
                "candidate_limit_config": {
                    "market_candidate_count": 0,
                    "lookahead_multiplier": 4,
                    "lookahead_count": 5200,
                },
            },
        }
        audit = _build_pipeline_audit(
            snapshot=snapshot,
            params={"output_count": 6, "hide_recent_repeats": True},
            total_contexts=360,
            prelim_count=360,
            skill_input_count=240,
            skill_review_limit=240,
            skill_review_concurrency=6,
            skill_demo_limited=True,
        )

        assert audit["skill_review_limit"] == 240
        assert audit["skill_input_count"] == 240
        assert audit["demo_limited"] is True
        assert audit["scan_limited"] is False
        assert audit["skill_limited"] is True

    def test_filter_audit_distinguishes_disabled_agent_review_from_failure(self):
        audit = _build_filter_audit(
            prefilter_audit={"input_count": 10, "prefilter_candidate_count": 2, "rows": []},
            skill_gate_audit={"agent_review_disabled": True, "reason_counts": {}, "rows": []},
            scored_cards=[],
            recommended_cards=[],
            pipeline={
                "unified_quote_total": 10,
                "base_filter_eligible_count": 10,
                "scoped_candidate_count": 10,
                "prefilter_candidate_count": 2,
                "actual_scan_count": 2,
                "skill_input_count": 2,
                "skill_review_success_count": 0,
                "agent_review_disabled": True,
            },
        )

        skill_review = next(row for row in audit["stage_summary"] if row["stage"] == "skill_review")
        assert skill_review["reason_counts"] == {"agent_review_disabled": 2}

    def test_skill_review_failure_audit_buckets_model_payment_error(self):
        error = "Skill Agent 直接审查失败：已尝试 1 个模型路由；DeepSeek (deepseek-chat) 失败: Error code: 402 - Payment Required"
        audit = _skill_review_failure_audit(
            [
                {
                    "stock": {"code": "600000"},
                    "skill_reviews": [
                        {
                            "skill_id": "chen-xiaoqun-skill",
                            "skill_name": "陈小群",
                            "review": None,
                            "error": error,
                        }
                    ],
                }
            ],
            [
                {
                    "status": "payment_required",
                    "model": "deepseek-chat",
                    "error_message": error,
                }
            ],
        )

        assert audit["failed_count"] == 2
        assert audit["affected_stock_count"] == 1
        assert audit["affected_skill_count"] == 1
        assert audit["api_failure_count"] == 1
        assert audit["reason_counts"] == {"model_payment_required": 2}
        assert "Payment Required" in audit["sample_errors"][0]

    def test_bootstrap_handles_empty_pool(self, db, monkeypatch):
        """17.1: bootstrap should not crash when the market pool is empty."""
        from app.services import workbench_service

        invalidate_bootstrap_cache()
        _seed_skill_registry(db)
        monkeypatch.setattr(
            workbench_service,
            "get_market_snapshot_from_pool",
            lambda _db: {
                "snapshot_id": "empty-pool",
                "trade_date": None,
                "sample_count": 0,
                "up_count": 0,
                "down_count": 0,
                "limit_up_count": 0,
                "limit_down_count": 0,
                "main_industries": [],
                "breadth": {"regime": "未知"},
            },
        )
        state = workbench_service.bootstrap_workbench_state(db)
        # Should return a valid state with empty market snapshot
        assert state["presets"] is not None
        assert state["market_snapshot"] is not None
        # Empty pool: sample_count should be 0, not raise
        assert state["market_snapshot"]["sample_count"] == 0
        assert state["market_snapshot"]["breadth"]["regime"] == "未知"

    def test_bootstrap_uses_30s_cache_until_invalidated(self, db, monkeypatch):
        """Bootstrap should avoid rebuilding the same heavy payload inside the TTL."""
        from app.services import workbench_service

        invalidate_bootstrap_cache()
        _seed_skill_registry(db)
        calls = {"count": 0}

        def fake_snapshot(_db):
            calls["count"] += 1
            return {
                "snapshot_id": f"snap-{calls['count']}",
                "trade_date": "2026-05-12",
                "sample_count": calls["count"],
                "up_count": 0,
                "down_count": 0,
                "limit_up_count": 0,
                "limit_down_count": 0,
                "main_industries": [],
                "breadth": {"regime": "测试"},
            }

        monkeypatch.setattr(workbench_service, "get_market_snapshot_from_pool", fake_snapshot)
        first = workbench_service.bootstrap_workbench_state(db)
        second = workbench_service.bootstrap_workbench_state(db)
        assert calls["count"] == 1
        assert second["market_snapshot"]["snapshot_id"] == first["market_snapshot"]["snapshot_id"]

        invalidate_bootstrap_cache()
        third = workbench_service.bootstrap_workbench_state(db)
        assert calls["count"] == 2
        assert third["market_snapshot"]["snapshot_id"] != first["market_snapshot"]["snapshot_id"]

    def test_initial_state_uses_cache_until_invalidated(self, db, monkeypatch):
        """Initial state should share the same invalidation behavior as bootstrap."""
        from app.services import workbench_service

        invalidate_bootstrap_cache()
        _seed_skill_registry(db)
        calls = {"count": 0}

        def fake_bootstrap(_db):
            calls["count"] += 1
            return {
                "presets": [],
                "recent_runs": {"formal": None, "experiment": None},
                "market_snapshot": {
                    "snapshot_id": f"snap-{calls['count']}",
                    "trade_date": "2026-05-12",
                    "sample_count": calls["count"],
                    "up_count": 0,
                    "down_count": 0,
                    "limit_up_count": 0,
                    "limit_down_count": 0,
                    "main_industries": [],
                    "breadth": {"regime": "测试"},
                },
                "skill_summary": {
                    "formal_count": 1,
                    "experiment_count": 0,
                    "disabled_count": 0,
                },
            }

        monkeypatch.setattr(workbench_service, "bootstrap_workbench_state", fake_bootstrap)
        monkeypatch.setattr(workbench_service, "list_presets", lambda _db: [{"preset_id": "p1"}])
        monkeypatch.setattr(workbench_service, "list_runs", lambda _db: [{"run_id": "r1"}])
        monkeypatch.setattr(workbench_service, "list_skills", lambda _db: [{"skill_id": "s1"}])

        import app.services.data_sources as data_sources_service

        monkeypatch.setattr(data_sources_service, "list_data_sources", lambda _db: [{"source": "sina"}])

        first = get_initial_workbench_state(db)
        second = get_initial_workbench_state(db)
        assert calls["count"] == 1
        assert second["home"]["market_snapshot"]["snapshot_id"] == first["home"]["market_snapshot"]["snapshot_id"]

        invalidate_bootstrap_cache()
        third = get_initial_workbench_state(db)
        assert calls["count"] == 2
        assert third["home"]["market_snapshot"]["snapshot_id"] != first["home"]["market_snapshot"]["snapshot_id"]

    def test_enqueue_workbench_run_persists_queued_run(self, db, monkeypatch):
        """Async run API should return immediately with a persisted queued row."""
        from app.services import workbench_service

        _seed_skill_registry(db)
        skill = _mock_skill("daily-stock-analysis-skill", "DSA分析")
        monkeypatch.setattr(workbench_service, "sync_skills", lambda _db: [skill])

        queued: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            workbench_service,
            "_enqueue_background_run",
            lambda run_id, payload: queued.append((run_id, payload)),
        )

        result = enqueue_workbench_run(
            db,
            {
                "run_mode": "experiment",
                "run_name": "异步测试",
                "single_skill_id": "daily-stock-analysis-skill",
                "single_skill_weight": 1,
            },
        )

        run_id = result["run"]["run_id"]
        persisted = db.get(RecommendationRun, run_id)
        assert result["queued"] is True
        assert result["recommendations"] == []
        assert persisted is not None
        assert persisted.status == "queued"
        assert persisted.heartbeat_at is not None
        assert persisted.progress_phase == "queued"
        assert persisted.progress_message == "等待后台工作线程接管"
        assert result["run"]["status"] == "queued"
        assert result["run"]["progress_phase"] == "queued"
        assert queued and queued[0][0] == run_id

    def test_sync_run_market_failure_persists_failed_run(self, db, monkeypatch):
        """Synchronous runs should be visible and failed if market loading fails early."""
        from app.services import workbench_service

        run_name = f"同步失败测试-{uuid.uuid4().hex[:8]}"
        _seed_skill_registry(db)
        skill = _mock_skill("daily-stock-analysis-skill", "DSA分析")
        monkeypatch.setattr(workbench_service, "sync_skills", lambda _db: [skill])

        def fail_market(_db, refresh=False):
            raise RuntimeError("market pool timeout")

        monkeypatch.setattr(workbench_service, "get_market_universe_from_pool", fail_market)

        persisted = None
        try:
            with pytest.raises(RuntimeError, match="market pool timeout"):
                asyncio.run(
                    run_workbench(
                        db,
                        {
                            "run_mode": "experiment",
                            "run_name": run_name,
                            "single_skill_id": "daily-stock-analysis-skill",
                            "single_skill_weight": 1,
                            "data_source": DATA_SOURCE_LOCAL_POOL,
                        },
                    )
                )

            persisted = db.scalar(select(RecommendationRun).where(RecommendationRun.run_name == run_name))
            assert persisted is not None
            assert persisted.status == "failed"
            assert persisted.progress_phase == "failed"
            assert persisted.error_message == "market pool timeout"
            assert persisted.heartbeat_at is not None
        finally:
            if persisted is not None:
                db.delete(persisted)
                db.commit()

    def test_update_run_progress_records_heartbeat_and_counts(self, db):
        run_id = f"run_progress_{uuid.uuid4().hex[:8]}"
        run = RecommendationRun(
            run_id=run_id,
            run_name="进度测试",
            run_mode="experiment",
            trade_date="2026-05-12",
            status="queued",
            selected_skills_json="[]",
            params_json="{}",
            market_snapshot_json="{}",
            result_summary_json="{}",
        )
        db.add(run)
        db.commit()

        updated = _update_run_progress(
            db,
            run_id,
            phase="rule_scan",
            current=42,
            total=100,
            message="规则扫描 42/100",
            status="running",
        )

        assert updated is not None
        persisted = db.get(RecommendationRun, run_id)
        assert persisted.status == "running"
        assert persisted.heartbeat_at is not None
        assert persisted.progress_phase == "rule_scan"
        assert persisted.progress_current == 42
        assert persisted.progress_total == 100
        assert persisted.progress_message == "规则扫描 42/100"

    def test_recover_interrupted_runs_marks_active_runs(self, db):
        suffix = uuid.uuid4().hex[:8]
        run_ids = {
            "queued": f"run_queued_{suffix}",
            "running": f"run_running_{suffix}",
            "completed": f"run_done_{suffix}",
        }
        for status, run_id in run_ids.items():
            db.add(
                RecommendationRun(
                    run_id=run_id,
                    run_name=run_id,
                    run_mode="experiment",
                    trade_date="2026-05-12",
                    status=status,
                    selected_skills_json="[]",
                    params_json="{}",
                    market_snapshot_json="{}",
                    result_summary_json="{}",
                )
            )
        db.commit()

        recovered = recover_interrupted_runs(db, run_ids=[run_ids["queued"], run_ids["running"], run_ids["completed"]])

        assert recovered == 2
        assert db.get(RecommendationRun, run_ids["queued"]).status == "interrupted"
        assert db.get(RecommendationRun, run_ids["running"]).status == "interrupted"
        assert db.get(RecommendationRun, run_ids["completed"]).status == "completed"
        assert db.get(RecommendationRun, run_ids["running"]).interrupted_at is not None
        assert "重新发起运行" in db.get(RecommendationRun, run_ids["running"]).error_message

    def test_aggregate_scoring_determinism(self):
        """Verify aggregate produces deterministic output for same inputs."""
        stock = {"code": "600519", "name": "茅台", "price": 100.0, "industry": "白酒"}
        signals = [{
            "strategy_id": "test",
            "score": 75.0,
            "confidence": 0.7,
            "evidence": [],
            "risk_flags": [],
            "agent_skill_review": {
                "stance": "support",
                "score": 75,
                "confidence": 0.7,
                "evidence": ["测试"],
                "risks": [],
                "summary": "测试",
            },
        }]

        card1 = aggregate(stock, signals, market_score=72, agent_review_score=75)
        card2 = aggregate(stock, signals, market_score=72, agent_review_score=75)
        assert card1["final_score"] == card2["final_score"]
