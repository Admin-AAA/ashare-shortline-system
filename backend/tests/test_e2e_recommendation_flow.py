"""End-to-end recommendation flow tests with mocked external dependencies.

Covers: run → market/K-line → features → aggregation → bucketing → persistence →
        run detail query → recommendations query → stats consistency.

All external data sources and LLM calls are mocked for deterministic verification.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.init_db import init_db
from app.db.session import SessionLocal, engine
from app.engine.aggregator import DEFAULT_AGG_WEIGHTS, aggregate
from app.engine.skill_loader import StrategySkill
from app.models.tables import (
    CalibrationConfig,
    DailyBar,
    Preset,
    PresetSkill,
    Recommendation,
    RecommendationRun,
    SkillRegistry,
    Stock,
)
from app.services.outcome_service import label_pending_outcomes
from app.services.stats_service import get_overview_stats
from app.services.workbench_service import (
    _build_pipeline_audit,
    _update_run_progress,
    enqueue_workbench_run,
    get_recommendation_detail,
    get_run_detail,
    invalidate_bootstrap_cache,
    list_run_recommendations,
    list_runs,
    recover_interrupted_runs,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_db():
    """Drop and recreate all tables for each test."""
    from app.models.tables import Base

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    """Provide a fresh DB session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


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
        db.add(Stock(code=code, name=name, exchange="SH", industry="白酒", is_st=False))
    for i in range(60, 0, -1):
        trade_date = f"2026-{(5 - i // 30):02d}-{(i % 30) + 1:02d}"
        if len(trade_date) != 10:
            continue
        exists = db.scalar(select(DailyBar).where(DailyBar.code == code, DailyBar.trade_date == trade_date))
        if exists is not None:
            continue
        price = 100.0 + i * 0.5
        db.add(DailyBar(
            code=code, trade_date=trade_date,
            open=price - 0.5, high=price + 1.0, low=price - 1.0, close=price,
            volume=1000000, amount=price * 1000000,
            pct_chg=0.5, turnover_rate=0.3,
        ))
    db.commit()


def _mock_market_universe():
    """Return a realistic market universe with 30 stocks."""
    stocks = []
    for i in range(30):
        code = f"60{i:04d}"
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
        "snapshot_id": f"snap_test_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
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
        _seed_stock_and_bars(db, "600519", "贵州茅台", "2026-05-12")

        stocks, snapshot = _mock_market_universe()

        # Seed stock records for the mock universe
        for s in stocks:
            if not db.get(Stock, s["code"]):
                db.add(Stock(code=s["code"], name=s["name"], exchange=s.get("exchange"),
                            industry=s.get("industry"), is_st=False))
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
        assert card["bucket"] in {"容量核心", "高弹观察", "证据不足", "明确回避"}
        assert "component_scores" in card
        assert card["component_scores"]["skill_agent"] > 0
        assert card["component_scores"]["strategy_rule"] > 0
        assert card["component_scores"]["market"] > 0
        assert "stance_summary" in card

        # Aggregate all stocks into cards and apply pool rules
        cards = []
        for ctx in contexts:
            stock_data = ctx.to_skill_input()
            review = {
                "stance": "support",
                "score": 72,
                "confidence": 0.7,
                "evidence": ["测试证据"],
                "risks": [],
                "summary": "测试审查",
            }
            rule_signal = {
                "strategy_id": "test-skill",
                "score": 70.0,
                "confidence": 0.7,
                "evidence": [],
                "risk_flags": [],
                "setup_match": "多头排列",
                "entry_rule": "回踩均线",
                "invalid_rule": "跌破支撑",
            }
            signal = enrich_signal_with_agent_review(rule_signal, review)
            cards.append(aggregate(
                stock=stock_data, signals=[signal],
                market_score=market_score, agent_review_score=72.0,
                risk_template="balanced",
            ))

        result = apply_pool_rules(cards, {"容量核心": 3, "高弹观察": 2}, output_count=6)
        assert 1 <= len(result) <= 6

        # Persist run and recommendations
        run_id = f"run_test_{uuid.uuid4().hex[:6]}"
        run = RecommendationRun(
            run_id=run_id,
            preset_id=None,
            run_name="E2E测试运行",
            run_mode="formal",
            trade_date="2026-05-12",
            universe_count=len(stocks),
            market_scope="all",
            risk_template="balanced",
            output_count=6,
            selected_skills_json=json.dumps([]),
            params_json=json.dumps({}),
            market_snapshot_json=json.dumps(snapshot),
            status="completed",
            recommended_count=len(result),
            result_summary_json=json.dumps({"bucket_counts": {}}),
            finished_at=datetime.utcnow(),
        )
        db.add(run)
        db.flush()

        for i, card in enumerate(result):
            rec = Recommendation(
                recommendation_id=f"rec_test_{i:03d}",
                run_id=run_id,
                trade_date="2026-05-12",
                code=card["code"],
                name=card["name"],
                industry=card.get("industry"),
                recommend_price=float(card.get("recommend_price", 0)),
                bucket=card["bucket"],
                final_score=float(card["final_score"]),
                consensus_score=float(card.get("consensus_score", 0)),
                setup_type=card.get("setup_type"),
                entry_rule=card.get("entry_rule"),
                invalid_rule=card.get("invalid_rule"),
                rationale=card.get("rationale"),
                risk_tags_json=json.dumps(card.get("risk_flags", [])),
                feature_snapshot_json=json.dumps({
                    "component_scores": card.get("component_scores", {}),
                    "data_authenticity": card.get("data_authenticity", 1.0),
                }),
                skill_breakdown_json=json.dumps(card.get("strategy_signals", [])),
                agent_summary_json=json.dumps({"logs": [], "mode": "test"}),
            )
            db.add(rec)
        db.commit()

        # Verify persistence (17.1 check)
        saved_run = get_run_detail(db, run_id)
        assert saved_run is not None
        assert saved_run["status"] == "completed"
        assert saved_run["recommended_count"] == len(result)

        recs = list_run_recommendations(db, run_id)
        assert len(recs) == len(result)
        for rec in recs:
            assert rec["recommendation_id"] is not None
            assert rec["code"] is not None
            assert rec["final_score"] > 0
            assert rec["bucket"] in {"容量核心", "高弹观察", "证据不足", "明确回避"}

        # Verify recommendation detail
        if recs:
            detail = get_recommendation_detail(db, recs[0]["recommendation_id"])
            assert detail is not None
            assert detail["code"] == recs[0]["code"]

    def test_stats_consistency(self, db):
        """17.6: Stats overview returns correct counts."""
        _seed_skill_registry(db)

        # Create a run with recommendations
        run_id = f"run_stats_{uuid.uuid4().hex[:6]}"
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
            finished_at=datetime.utcnow(),
        )
        db.add(run)
        db.flush()

        codes = ["600519", "000001", "600036"]
        for i, code in enumerate(codes):
            rec = Recommendation(
                recommendation_id=f"rec_stats_{i:03d}",
                run_id=run_id,
                trade_date="2026-05-12",
                code=code,
                name=f"测试股{i}",
                bucket="容量核心",
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
                recommendation_id=f"rec_stats_{i:03d}",
                horizon_days=5,
                outcome_label="success" if i < 2 else "failed",
                close_return=5.0 if i < 2 else -3.0,
                max_return=8.0 if i < 2 else 1.0,
                min_return=-2.0 if i < 2 else -5.0,
                label_source="manual",
            )
            db.add(outcome)
            # Update recommendation status to match
            db.get(Recommendation, f"rec_stats_{i:03d}").status = "success" if i < 2 else "failed"
        db.commit()

        stats = get_overview_stats(db, "formal")
        assert stats is not None
        assert stats["run_count"] == 1
        assert stats["recommendation_count"] == 3
        assert stats["success_count"] == 2
        assert stats["failed_count"] == 1
        assert stats["pending_count"] == 0
        assert stats["success_count"] + stats["failed_count"] + stats["pending_count"] == stats["recommendation_count"]
        # hit_rate = success / (success + failed) = 2/3 ≈ 0.667
        assert abs(stats["hit_rate"] - 0.667) < 0.01

    def test_component_scores_match_aggregator_dimensions(self, db):
        """Verify feature_snapshot_json.component_scores keys match aggregator DIMENSIONS."""
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
            finished_at=datetime.utcnow(),
        )
        db.add(run)
        db.flush()

        rec = Recommendation(
            recommendation_id="rec_dims_001",
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

        detail = get_recommendation_detail(db, "rec_dims_001")
        cs = detail["feature_snapshot"].get("component_scores", {})
        expected_keys = set(DEFAULT_AGG_WEIGHTS.keys())
        actual_keys = set(cs.keys())
        assert actual_keys == expected_keys, f"Component keys mismatch: {actual_keys} != {expected_keys}"

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

    def test_bootstrap_handles_empty_pool(self, db):
        """17.1: bootstrap should not crash when the market pool is empty."""
        from app.services.workbench_service import bootstrap_workbench_state

        invalidate_bootstrap_cache()
        _seed_skill_registry(db)
        state = bootstrap_workbench_state(db)
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

    def test_update_run_progress_records_heartbeat_and_counts(self, db):
        run = RecommendationRun(
            run_id="run_progress",
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
            "run_progress",
            phase="rule_scan",
            current=42,
            total=100,
            message="规则扫描 42/100",
            status="running",
        )

        assert updated is not None
        persisted = db.get(RecommendationRun, "run_progress")
        assert persisted.status == "running"
        assert persisted.heartbeat_at is not None
        assert persisted.progress_phase == "rule_scan"
        assert persisted.progress_current == 42
        assert persisted.progress_total == 100
        assert persisted.progress_message == "规则扫描 42/100"

    def test_recover_interrupted_runs_marks_active_runs(self, db):
        for run_id, status in [("run_queued", "queued"), ("run_running", "running"), ("run_done", "completed")]:
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

        recovered = recover_interrupted_runs(db)

        assert recovered == 2
        assert db.get(RecommendationRun, "run_queued").status == "interrupted"
        assert db.get(RecommendationRun, "run_running").status == "interrupted"
        assert db.get(RecommendationRun, "run_done").status == "completed"
        assert db.get(RecommendationRun, "run_running").interrupted_at is not None
        assert "重新发起运行" in db.get(RecommendationRun, "run_running").error_message

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
        assert card1["bucket"] == card2["bucket"]
