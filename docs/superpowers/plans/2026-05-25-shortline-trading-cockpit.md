# Shortline Trading Cockpit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the demo workbench with a production-grade shortline trading cockpit while splitting backend recommendation, data-pool, validation, and frontend view-model boundaries.

**Architecture:** Backend changes introduce Pydantic dashboard DTOs, projection services, data-pool readiness services, safety guards, and a dashboard API while extracting low-risk boundaries from `workbench_service.py`. Frontend changes replace the monolithic workbench with an `AppShell`, theme tokens, cockpit dashboard feature modules, and stable dashboard API adapters. Existing raw recommendation JSON remains in persistence for MVP, but all cockpit screens consume normalized projection DTOs.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Pydantic 2, pytest, React 19, TypeScript, Vite, ECharts, lucide-react.

---

## Execution Rules

- Create or coordinate subagents with explicit `model: gpt-5.5`.
- Use the project virtual environment for backend tests: `cd backend && ./.venv/bin/python -m pytest ...`.
- Do not edit Maven `pom.xml` files.
- After each task that edits files, stage related changes with `git add`.
- Do not deploy to the cloud server unless the user asks again.
- Do not run destructive production operations. Safety scripts default to dry-run.

## File Structure Map

### Backend Files

- Create: `backend/app/schemas/dashboard.py` for cockpit DTOs.
- Create: `backend/app/api/dashboard.py` for `/api/dashboard/*`.
- Modify: `backend/app/main.py` to register dashboard router.
- Create: `backend/app/services/recommendations/projection.py` for recommendation and run projection.
- Create: `backend/app/services/recommendations/explanation.py` for normalized explanation helpers.
- Create: `backend/app/services/data_pool/readiness.py` for data-pool readiness projections.
- Create: `backend/app/services/data_pool/health_projection.py` for data source, task, and alert projections.
- Create: `backend/app/services/validation/safety.py` for dry-run, batch, and destructive-operation guards.
- Modify: `backend/scripts/acceptance_check.py` for safe batch execution and cleanup.
- Modify: `backend/scripts/rebuild_data_pool.py` for destructive-operation guards.
- Create: `backend/tests/test_dashboard_projection.py`.
- Create: `backend/tests/test_data_pool_readiness_projection.py`.
- Create: `backend/tests/test_validation_safety.py`.

### Frontend Files

- Replace: `frontend/src/App.tsx` with a thin app entry that renders `AuthGate` and `AppShell`.
- Create: `frontend/src/app/AuthGate.tsx`.
- Create: `frontend/src/app/AppShell.tsx`.
- Create: `frontend/src/app/nav.ts`.
- Create: `frontend/src/api/dashboard.ts`.
- Modify: `frontend/src/api/client.ts` to expose dashboard calls and keep legacy calls used during transition.
- Modify: `frontend/src/types.ts` to add dashboard DTOs.
- Create: `frontend/src/shared/theme/useThemePreference.ts`.
- Create: `frontend/src/shared/theme/initTheme.ts`.
- Create: `frontend/src/shared/ui/*.tsx` shared primitives.
- Create: `frontend/src/features/dashboard/DashboardPage.tsx`.
- Create: `frontend/src/features/dashboard/components/*.tsx`.
- Create: `frontend/src/features/dashboard/hooks/useDashboardViewModel.ts`.
- Replace: `frontend/src/styles/app.css` content with imports for tokenized cockpit styles.
- Create: `frontend/src/styles/tokens.css`.
- Create: `frontend/src/styles/themes.css`.
- Create: `frontend/src/styles/cockpit.css`.
- Modify: `frontend/src/main.tsx` to initialize theme before rendering.

### Documentation Files

- Modify: `README.md`.
- Modify: `docs/architecture.md`.
- Modify: `docs/上线验收标准.md`.

---

## Task 0: Prepare Isolated Implementation Branch

**Files:**
- No source edits in this task.

- [ ] **Step 1: Verify clean baseline**

Run:

```bash
git status --short
git branch --show-current
git log --oneline -3
```

Expected: clean status, branch `main`, latest commit includes `docs: design shortline trading cockpit`.

- [ ] **Step 2: Create implementation branch**

Run:

```bash
git switch -c codex/shortline-trading-cockpit
```

Expected: branch switches to `codex/shortline-trading-cockpit`.

- [ ] **Step 3: Run safe baseline checks**

Run:

```bash
cd frontend && npm run build
cd ../backend && ./.venv/bin/python -m pytest -q tests/test_feature_builder.py tests/test_aggregator.py tests/test_hallucination_check.py tests/test_research_context.py
```

Expected: frontend build succeeds; selected backend tests pass. If a baseline failure appears, record the failing command and continue only after confirming it is unrelated to the cockpit work.

---

## Task 1: Add Dashboard DTO Contracts

**Files:**
- Create: `backend/app/schemas/dashboard.py`
- Test: `backend/tests/test_dashboard_projection.py`

- [ ] **Step 1: Write DTO contract tests**

Create `backend/tests/test_dashboard_projection.py` with:

```python
from app.schemas.dashboard import (
    CockpitRecommendation,
    DashboardOverview,
    DataQualitySummary,
    MarketTemperature,
    RecommendationExplanation,
    RiskRadar,
    RunProgress,
    SkillReviewSummary,
)


def test_dashboard_overview_contract_accepts_cockpit_sections():
    overview = DashboardOverview(
        market=MarketTemperature(
            trade_date="2026-05-25",
            sample_count=4200,
            up_count=2600,
            down_count=1300,
            limit_up_count=68,
            limit_down_count=4,
            up_ratio=0.619,
            heat_score=74.2,
            regime="active",
            main_industries=["机器人", "半导体"],
        ),
        recommendations=[
            CockpitRecommendation(
                recommendation_id="rec-1",
                run_id="run-1",
                trade_date="2026-05-25",
                code="000001",
                name="平安银行",
                industry="银行",
                final_score=82.4,
                score_gap=10.4,
                bucket="core",
                setup_type="趋势突破",
                rationale="规则与 Agent 共识支持，市场环境可接受。",
                risk_level="medium",
                risk_tags=["放量过快"],
                info_tags=["日K覆盖完整"],
                status="pending",
                watch_status="none",
                consensus_label="一致",
                data_quality=DataQualitySummary(
                    quote_status="fresh",
                    quote_source_count=2,
                    kline_status="covered",
                    kline_days=90,
                    auxiliary_coverage=0.8,
                    missing_fields=[],
                ),
            )
        ],
        risk_radar=RiskRadar(high=1, medium=2, info=3, tags=["放量过快"]),
        current_run=RunProgress(
            run_id="run-1",
            status="completed",
            phase="persist",
            progress_pct=100,
            message="完成",
            heartbeat_stale=False,
            resumable=False,
        ),
        data_health={"status": "ready"},
        task_health={"running_count": 0},
        validation={"last_status": "not_run"},
    )

    assert overview.market.heat_score == 74.2
    assert overview.recommendations[0].data_quality.quote_source_count == 2


def test_recommendation_explanation_contract_groups_agent_reviews():
    explanation = RecommendationExplanation(
        recommendation_id="rec-1",
        score_breakdown={"agent": 31.2, "rule": 22.5, "market": 12.0, "risk": -6.0, "evidence": 8.0},
        skill_reviews=[
            SkillReviewSummary(
                skill_id="daily-stock-analysis-skill",
                skill_name="技术评分",
                stance="support",
                score=78.0,
                confidence=0.72,
                supports=["均线多头"],
                risks=["短线乖离"],
                action="watch",
            )
        ],
        pipeline_trace=[],
        trade_plan={"entry_rule": "回踩不破 5 日线", "invalid_rule": "跌破 10 日线"},
        review_feedback={"watch_status": "none"},
    )

    assert explanation.skill_reviews[0].stance == "support"
    assert explanation.trade_plan["invalid_rule"] == "跌破 10 日线"
```

- [ ] **Step 2: Run DTO tests and verify they fail**

Run:

```bash
cd backend && ./.venv/bin/python -m pytest -q tests/test_dashboard_projection.py
```

Expected: fail with `ModuleNotFoundError: No module named 'app.schemas.dashboard'`.

- [ ] **Step 3: Implement dashboard DTOs**

Create `backend/app/schemas/dashboard.py` with:

```python
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


RiskLevel = Literal["low", "medium", "high", "unknown"]


class DataQualitySummary(BaseModel):
    quote_status: str = "unknown"
    quote_source_count: int = 0
    kline_status: str = "unknown"
    kline_days: int = 0
    auxiliary_coverage: float = 0.0
    missing_fields: list[str] = Field(default_factory=list)


class SkillReviewSummary(BaseModel):
    skill_id: str
    skill_name: str
    stance: str = "unknown"
    score: float = 0.0
    confidence: float = 0.0
    supports: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    action: str = "watch"


class CandidateTraceRow(BaseModel):
    code: str
    stage: str
    passed: bool
    reason: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)


class MarketTemperature(BaseModel):
    trade_date: str
    sample_count: int = 0
    up_count: int = 0
    down_count: int = 0
    limit_up_count: int = 0
    limit_down_count: int = 0
    up_ratio: float = 0.0
    heat_score: float = 0.0
    regime: str = "unknown"
    main_industries: list[str] = Field(default_factory=list)


class CockpitRecommendation(BaseModel):
    recommendation_id: str
    run_id: str
    trade_date: str
    code: str
    name: str
    industry: str | None = None
    final_score: float = 0.0
    score_gap: float | None = None
    bucket: str = ""
    setup_type: str | None = None
    rationale: str = ""
    risk_level: RiskLevel = "unknown"
    risk_tags: list[str] = Field(default_factory=list)
    info_tags: list[str] = Field(default_factory=list)
    status: str = "pending"
    watch_status: str = "none"
    consensus_label: str = "未知"
    data_quality: DataQualitySummary = Field(default_factory=DataQualitySummary)


class RiskRadar(BaseModel):
    high: int = 0
    medium: int = 0
    info: int = 0
    tags: list[str] = Field(default_factory=list)


class RunProgress(BaseModel):
    run_id: str | None = None
    status: str = "idle"
    phase: str | None = None
    progress_pct: float | None = None
    message: str = ""
    heartbeat_stale: bool = False
    resumable: bool = False


class PipelineStageSummary(BaseModel):
    stage: str
    label: str
    input_count: int = 0
    pass_count: int = 0
    filtered_count: int = 0
    reason_counts: dict[str, int] = Field(default_factory=dict)


class RecommendationExplanation(BaseModel):
    recommendation_id: str
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    skill_reviews: list[SkillReviewSummary] = Field(default_factory=list)
    pipeline_trace: list[CandidateTraceRow] = Field(default_factory=list)
    trade_plan: dict[str, Any] = Field(default_factory=dict)
    review_feedback: dict[str, Any] = Field(default_factory=dict)


class DashboardOverview(BaseModel):
    market: MarketTemperature
    recommendations: list[CockpitRecommendation] = Field(default_factory=list)
    risk_radar: RiskRadar = Field(default_factory=RiskRadar)
    current_run: RunProgress = Field(default_factory=RunProgress)
    data_health: dict[str, Any] = Field(default_factory=dict)
    task_health: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 4: Run DTO tests and verify they pass**

Run:

```bash
cd backend && ./.venv/bin/python -m pytest -q tests/test_dashboard_projection.py
```

Expected: pass.

- [ ] **Step 5: Stage and commit**

Run:

```bash
git add backend/app/schemas/dashboard.py backend/tests/test_dashboard_projection.py
git commit -m "feat: add dashboard dto contracts"
```

---

## Task 2: Build Recommendation Projection Services

**Files:**
- Create: `backend/app/services/recommendations/__init__.py`
- Create: `backend/app/services/recommendations/explanation.py`
- Create: `backend/app/services/recommendations/projection.py`
- Modify: `backend/tests/test_dashboard_projection.py`

- [ ] **Step 1: Add projection unit tests**

Append to `backend/tests/test_dashboard_projection.py`:

```python
from datetime import datetime
import json
from types import SimpleNamespace

from app.services.recommendations.projection import (
    build_cockpit_recommendation,
    build_market_temperature,
    build_recommendation_explanation,
    build_risk_radar,
    build_run_progress,
)


def test_build_market_temperature_calculates_heat_and_ratio():
    snapshot = {
        "trade_date": "2026-05-25",
        "sample_count": 100,
        "up_count": 62,
        "down_count": 30,
        "limit_up_count": 7,
        "limit_down_count": 1,
        "main_industries": ["机器人"],
        "breadth": {"market_regime": "active"},
    }

    result = build_market_temperature(snapshot)

    assert result.up_ratio == 0.62
    assert result.heat_score > 60
    assert result.regime == "active"


def test_build_cockpit_recommendation_normalizes_json_fields():
    rec = SimpleNamespace(
        recommendation_id="rec-1",
        run_id="run-1",
        trade_date="2026-05-25",
        code="000001",
        name="平安银行",
        industry="银行",
        final_score=82.0,
        setup_type="趋势突破",
        rationale="规则和 Agent 共识支持。",
        risk_tags_json=json.dumps(["短线放量"], ensure_ascii=False),
        info_tags_json=json.dumps(["日K覆盖完整"], ensure_ascii=False),
        status="pending",
        recent_repeat=False,
        feature_snapshot_json=json.dumps({
            "score_line_meta": {"effective_score_line": 72.0},
            "data_quality": {
                "quote_cache_status": "fresh",
                "quote_source_count": 2,
                "kline_cache_status": "covered",
                "kline_days": 90,
            },
        }, ensure_ascii=False),
        skill_breakdown_json=json.dumps([
            {"strategy_id": "skill-a", "strategy_name": "Skill A", "score": 80, "agent_skill_review": {"stance": "support"}}
        ], ensure_ascii=False),
    )
    watch = SimpleNamespace(watch_status="watching")

    result = build_cockpit_recommendation(rec, watch)

    assert result.score_gap == 10.0
    assert result.risk_level == "medium"
    assert result.consensus_label == "一致"
    assert result.data_quality.quote_source_count == 2


def test_build_run_progress_marks_stale_and_resumable():
    run = {
        "run_id": "run-1",
        "status": "interrupted",
        "progress_phase": "skill_review",
        "progress_pct": 45,
        "progress_message": "审查中",
        "is_stale": True,
    }

    result = build_run_progress(run)

    assert result.status == "interrupted"
    assert result.heartbeat_stale is True
    assert result.resumable is True


def test_build_risk_radar_counts_levels():
    recs = [
        CockpitRecommendation(
            recommendation_id="r1", run_id="run", trade_date="2026-05-25", code="000001", name="A",
            risk_level="high", risk_tags=["高位加速"], data_quality=DataQualitySummary(),
        ),
        CockpitRecommendation(
            recommendation_id="r2", run_id="run", trade_date="2026-05-25", code="000002", name="B",
            risk_level="medium", risk_tags=["放量过快"], data_quality=DataQualitySummary(),
        ),
    ]

    radar = build_risk_radar(recs)

    assert radar.high == 1
    assert radar.medium == 1
    assert radar.tags == ["高位加速", "放量过快"]
```

- [ ] **Step 2: Run projection tests and verify they fail**

Run:

```bash
cd backend && ./.venv/bin/python -m pytest -q tests/test_dashboard_projection.py
```

Expected: fail because `app.services.recommendations.projection` does not exist.

- [ ] **Step 3: Implement explanation helpers**

Create `backend/app/services/recommendations/__init__.py` as an empty package marker.

Create `backend/app/services/recommendations/explanation.py` with:

```python
from __future__ import annotations

import json
from typing import Any


def loads_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def risk_level_from_tags(tags: list[str]) -> str:
    joined = " ".join(str(item) for item in tags)
    high_terms = ("退市", "监管", "跌停", "重大利空", "高位分歧", "断板")
    medium_terms = ("放量", "乖离", "回撤", "减持", "解禁", "分歧")
    if any(term in joined for term in high_terms):
        return "high"
    if tags or any(term in joined for term in medium_terms):
        return "medium"
    return "low"


def consensus_label(skill_breakdown: list[dict[str, Any]]) -> str:
    stances = []
    for row in skill_breakdown:
        review = row.get("agent_skill_review") or {}
        stance = str(review.get("stance") or "").lower()
        if stance:
            stances.append(stance)
    support_count = sum(1 for item in stances if "support" in item)
    oppose_count = sum(1 for item in stances if "oppose" in item or item == "veto")
    if support_count and oppose_count:
        return "多空冲突"
    if len(set(stances)) <= 1 and stances:
        return "一致"
    if stances:
        return "轻微分歧"
    return "未知"


def data_quality_summary(feature_snapshot: dict[str, Any]) -> dict[str, Any]:
    quality = feature_snapshot.get("data_quality") or {}
    source_trace = feature_snapshot.get("source_trace") or {}
    missing = quality.get("missing_fields") or feature_snapshot.get("missing_fields") or []
    return {
        "quote_status": str(quality.get("quote_cache_status") or source_trace.get("quote_cache_status") or "unknown"),
        "quote_source_count": int(quality.get("quote_source_count") or source_trace.get("quote_source_count") or 0),
        "kline_status": str(quality.get("kline_cache_status") or source_trace.get("kline_cache_status") or "unknown"),
        "kline_days": int(quality.get("kline_days") or feature_snapshot.get("kline_days") or 0),
        "auxiliary_coverage": float(quality.get("auxiliary_coverage") or feature_snapshot.get("auxiliary_coverage") or 0),
        "missing_fields": [str(item) for item in missing],
    }
```

- [ ] **Step 4: Implement projection service**

Create `backend/app/services/recommendations/projection.py` with:

```python
from __future__ import annotations

from typing import Any

from app.schemas.dashboard import (
    CandidateTraceRow,
    CockpitRecommendation,
    DashboardOverview,
    DataQualitySummary,
    MarketTemperature,
    RecommendationExplanation,
    RiskRadar,
    RunProgress,
    SkillReviewSummary,
)
from app.services.recommendations.explanation import (
    consensus_label,
    data_quality_summary,
    loads_json,
    risk_level_from_tags,
)


def build_market_temperature(snapshot: dict[str, Any] | None) -> MarketTemperature:
    snapshot = snapshot or {}
    sample_count = int(snapshot.get("sample_count") or 0)
    up_count = int(snapshot.get("up_count") or 0)
    down_count = int(snapshot.get("down_count") or 0)
    limit_up_count = int(snapshot.get("limit_up_count") or 0)
    limit_down_count = int(snapshot.get("limit_down_count") or 0)
    breadth = snapshot.get("breadth") or {}
    up_ratio = round(up_count / sample_count, 3) if sample_count else 0.0
    heat_score = round(min(100.0, max(0.0, up_ratio * 70 + limit_up_count * 0.4 - limit_down_count * 0.8)), 2)
    return MarketTemperature(
        trade_date=str(snapshot.get("trade_date") or ""),
        sample_count=sample_count,
        up_count=up_count,
        down_count=down_count,
        limit_up_count=limit_up_count,
        limit_down_count=limit_down_count,
        up_ratio=up_ratio,
        heat_score=heat_score,
        regime=str(breadth.get("market_regime") or breadth.get("market_sentiment") or "unknown"),
        main_industries=[str(item) for item in snapshot.get("main_industries") or []],
    )


def build_cockpit_recommendation(rec: Any, watch: Any | None = None) -> CockpitRecommendation:
    feature_snapshot = loads_json(getattr(rec, "feature_snapshot_json", "{}"), {})
    skill_breakdown = loads_json(getattr(rec, "skill_breakdown_json", "[]"), [])
    risk_tags = [str(item) for item in loads_json(getattr(rec, "risk_tags_json", "[]"), [])]
    info_tags = [str(item) for item in loads_json(getattr(rec, "info_tags_json", "[]"), [])]
    score_line = feature_snapshot.get("score_line_meta") or {}
    effective_score_line = score_line.get("effective_score_line")
    score_gap = None
    if effective_score_line is not None:
        score_gap = round(float(getattr(rec, "final_score", 0) or 0) - float(effective_score_line), 2)
    return CockpitRecommendation(
        recommendation_id=str(getattr(rec, "recommendation_id")),
        run_id=str(getattr(rec, "run_id")),
        trade_date=str(getattr(rec, "trade_date")),
        code=str(getattr(rec, "code")),
        name=str(getattr(rec, "name")),
        industry=getattr(rec, "industry", None),
        final_score=float(getattr(rec, "final_score", 0) or 0),
        score_gap=score_gap,
        bucket=str(getattr(rec, "bucket", "") or ""),
        setup_type=getattr(rec, "setup_type", None),
        rationale=str(getattr(rec, "rationale", "") or ""),
        risk_level=risk_level_from_tags(risk_tags),
        risk_tags=risk_tags,
        info_tags=info_tags,
        status=str(getattr(rec, "status", "pending") or "pending"),
        watch_status=str(getattr(watch, "watch_status", "none") if watch else "none"),
        consensus_label=consensus_label(skill_breakdown),
        data_quality=DataQualitySummary(**data_quality_summary(feature_snapshot)),
    )


def build_skill_reviews(skill_breakdown: list[dict[str, Any]]) -> list[SkillReviewSummary]:
    reviews: list[SkillReviewSummary] = []
    for row in skill_breakdown:
        review = row.get("agent_skill_review") or {}
        supports = review.get("supports") or review.get("evidence") or []
        risks = review.get("risks") or []
        reviews.append(
            SkillReviewSummary(
                skill_id=str(row.get("strategy_id") or row.get("skill_id") or row.get("version_id") or ""),
                skill_name=str(row.get("strategy_name") or row.get("skill_name") or "未知 Skill"),
                stance=str(review.get("stance") or row.get("stance") or "unknown"),
                score=float(review.get("score") or row.get("score") or 0),
                confidence=float(review.get("confidence") or 0),
                supports=[str(item) for item in supports],
                risks=[str(item) for item in risks],
                action=str(review.get("action") or "watch"),
            )
        )
    return reviews


def build_recommendation_explanation(rec: Any, trace_rows: list[dict[str, Any]] | None = None, watch: Any | None = None) -> RecommendationExplanation:
    feature_snapshot = loads_json(getattr(rec, "feature_snapshot_json", "{}"), {})
    skill_breakdown = loads_json(getattr(rec, "skill_breakdown_json", "[]"), [])
    trace = [
        CandidateTraceRow(
            code=str(row.get("code") or getattr(rec, "code", "")),
            stage=str(row.get("stage") or ""),
            passed=bool(row.get("passed")),
            reason=str(row.get("reason") or ""),
            metrics=dict(row.get("metrics") or {}),
        )
        for row in (trace_rows or [])
    ]
    return RecommendationExplanation(
        recommendation_id=str(getattr(rec, "recommendation_id")),
        score_breakdown={k: float(v) for k, v in (feature_snapshot.get("component_scores") or feature_snapshot.get("score_breakdown") or {}).items() if isinstance(v, (int, float))},
        skill_reviews=build_skill_reviews(skill_breakdown),
        pipeline_trace=trace,
        trade_plan={
            "entry_rule": getattr(rec, "entry_rule", None),
            "invalid_rule": getattr(rec, "invalid_rule", None),
            "stop_loss": feature_snapshot.get("stop_loss"),
            "take_profit": feature_snapshot.get("take_profit"),
        },
        review_feedback={"watch_status": getattr(watch, "watch_status", "none") if watch else "none"},
    )


def build_run_progress(run: dict[str, Any] | None) -> RunProgress:
    if not run:
        return RunProgress()
    status = str(run.get("status") or "idle")
    return RunProgress(
        run_id=run.get("run_id"),
        status=status,
        phase=run.get("progress_phase") or run.get("checkpoint_phase"),
        progress_pct=run.get("progress_pct"),
        message=str(run.get("progress_message") or ""),
        heartbeat_stale=bool(run.get("is_stale")),
        resumable=status in {"interrupted", "failed"},
    )


def build_risk_radar(recommendations: list[CockpitRecommendation]) -> RiskRadar:
    tags: list[str] = []
    high = medium = info = 0
    for item in recommendations:
        if item.risk_level == "high":
            high += 1
        elif item.risk_level == "medium":
            medium += 1
        else:
            info += len(item.info_tags)
        for tag in item.risk_tags:
            if tag not in tags:
                tags.append(tag)
    return RiskRadar(high=high, medium=medium, info=info, tags=tags[:12])
```

- [ ] **Step 5: Run projection tests**

Run:

```bash
cd backend && ./.venv/bin/python -m pytest -q tests/test_dashboard_projection.py
```

Expected: pass.

- [ ] **Step 6: Stage and commit**

Run:

```bash
git add backend/app/services/recommendations backend/tests/test_dashboard_projection.py
git commit -m "feat: project recommendations for cockpit"
```

---

## Task 3: Add Data Pool and Task Health Projections

**Files:**
- Create: `backend/app/services/data_pool/__init__.py`
- Create: `backend/app/services/data_pool/readiness.py`
- Create: `backend/app/services/data_pool/health_projection.py`
- Test: `backend/tests/test_data_pool_readiness_projection.py`

- [ ] **Step 1: Write data-pool projection tests**

Create `backend/tests/test_data_pool_readiness_projection.py` with:

```python
from app.services.data_pool.health_projection import summarize_alerts, summarize_tasks
from app.services.data_pool.readiness import summarize_pool_status


def test_summarize_pool_status_reports_ready_counts():
    status = {
        "unified_quote_count": 4300,
        "active_unified_quote_count": 4100,
        "eligible_quote_count": 2600,
        "daily_bar_coverage_rate": 0.92,
        "eligible_daily_bar_coverage_rate": 0.96,
        "recommendation_readiness": {
            "status": "ready",
            "can_run_local_recommendation": True,
            "messages": ["本地池可运行"],
        },
    }

    summary = summarize_pool_status(status)

    assert summary["status"] == "ready"
    assert summary["quote_total"] == 4300
    assert summary["eligible_quote_count"] == 2600
    assert summary["can_run_local_recommendation"] is True
    assert summary["bar_coverage_pct"] == 92.0


def test_summarize_alerts_groups_severity():
    alerts = [
        {"severity": "high", "message": "报价源全部失败"},
        {"severity": "medium", "message": "日K覆盖不足"},
    ]

    summary = summarize_alerts(alerts)

    assert summary["high"] == 1
    assert summary["medium"] == 1
    assert summary["messages"] == ["报价源全部失败", "日K覆盖不足"]


def test_summarize_tasks_counts_running_and_failures():
    tasks = [
        {"task_id": "a", "enabled": True, "is_running": True, "last_status": "completed"},
        {"task_id": "b", "enabled": True, "is_running": False, "last_status": "failed", "last_error": "boom"},
    ]

    summary = summarize_tasks(tasks)

    assert summary["enabled_count"] == 2
    assert summary["running_count"] == 1
    assert summary["failed_count"] == 1
    assert summary["last_errors"] == ["boom"]
```

- [ ] **Step 2: Run data-pool tests and verify they fail**

Run:

```bash
cd backend && ./.venv/bin/python -m pytest -q tests/test_data_pool_readiness_projection.py
```

Expected: fail because `app.services.data_pool` does not exist.

- [ ] **Step 3: Implement readiness projection**

Create `backend/app/services/data_pool/__init__.py` as an empty package marker.

Create `backend/app/services/data_pool/readiness.py` with:

```python
from __future__ import annotations

from typing import Any


def _pct(value: Any) -> float:
    try:
        return round(float(value or 0) * 100, 2)
    except Exception:
        return 0.0


def summarize_pool_status(status: dict[str, Any]) -> dict[str, Any]:
    readiness = status.get("recommendation_readiness") or {}
    return {
        "status": str(readiness.get("status") or status.get("status") or "unknown"),
        "can_run_local_recommendation": bool(readiness.get("can_run_local_recommendation")),
        "quote_total": int(status.get("unified_quote_count") or status.get("stock_quote_count") or 0),
        "active_quote_count": int(status.get("active_unified_quote_count") or 0),
        "eligible_quote_count": int(status.get("eligible_quote_count") or 0),
        "bar_coverage_pct": _pct(status.get("daily_bar_coverage_rate")),
        "eligible_bar_coverage_pct": _pct(status.get("eligible_daily_bar_coverage_rate")),
        "messages": [str(item) for item in readiness.get("messages") or []],
        "examples": readiness.get("kline", {}).get("examples", []) if isinstance(readiness.get("kline"), dict) else [],
    }
```

- [ ] **Step 4: Implement health projection**

Create `backend/app/services/data_pool/health_projection.py` with:

```python
from __future__ import annotations

from typing import Any


def summarize_alerts(alerts: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {"high": 0, "medium": 0, "low": 0, "messages": []}
    for alert in alerts:
        severity = str(alert.get("severity") or "low")
        if severity not in {"high", "medium", "low"}:
            severity = "low"
        summary[severity] += 1
        message = str(alert.get("message") or "")
        if message:
            summary["messages"].append(message)
    return summary


def summarize_tasks(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    enabled = [task for task in tasks if task.get("enabled")]
    running = [task for task in enabled if task.get("is_running")]
    failed = [task for task in enabled if task.get("last_status") == "failed"]
    return {
        "enabled_count": len(enabled),
        "running_count": len(running),
        "failed_count": len(failed),
        "last_errors": [str(task.get("last_error")) for task in failed if task.get("last_error")],
    }
```

- [ ] **Step 5: Run data-pool tests**

Run:

```bash
cd backend && ./.venv/bin/python -m pytest -q tests/test_data_pool_readiness_projection.py
```

Expected: pass.

- [ ] **Step 6: Stage and commit**

Run:

```bash
git add backend/app/services/data_pool backend/tests/test_data_pool_readiness_projection.py
git commit -m "feat: summarize data pool health for cockpit"
```

---

## Task 4: Add Dashboard API

**Files:**
- Create: `backend/app/api/dashboard.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_dashboard_projection.py`

- [ ] **Step 1: Add API composition tests**

Append to `backend/tests/test_dashboard_projection.py`:

```python
from app.api.dashboard import compose_overview


def test_compose_overview_builds_all_cockpit_sections():
    overview = compose_overview(
        market_snapshot={
            "trade_date": "2026-05-25",
            "sample_count": 100,
            "up_count": 50,
            "down_count": 40,
            "limit_up_count": 5,
            "limit_down_count": 1,
            "main_industries": ["机器人"],
            "breadth": {"market_regime": "active"},
        },
        recent_run={"run_id": "run-1", "status": "completed", "progress_pct": 100},
        recommendations=[],
        data_health={"status": "ready"},
        task_health={"running_count": 0},
        validation={"last_status": "not_run"},
    )

    assert overview.market.trade_date == "2026-05-25"
    assert overview.current_run.status == "completed"
    assert overview.data_health["status"] == "ready"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
cd backend && ./.venv/bin/python -m pytest -q tests/test_dashboard_projection.py
```

Expected: fail because `app.api.dashboard` does not exist.

- [ ] **Step 3: Implement dashboard API module**

Create `backend/app/api/dashboard.py` with:

```python
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.dashboard import DashboardOverview
from app.services.data_pool.health_projection import summarize_alerts, summarize_tasks
from app.services.data_pool.readiness import summarize_pool_status
from app.services.recommendations.projection import (
    build_cockpit_recommendation,
    build_market_temperature,
    build_risk_radar,
    build_run_progress,
)
from app.services.workbench_service import list_run_recommendations, list_runs

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def compose_overview(
    *,
    market_snapshot: dict[str, Any],
    recent_run: dict[str, Any] | None,
    recommendations: list[Any],
    data_health: dict[str, Any],
    task_health: dict[str, Any],
    validation: dict[str, Any],
) -> DashboardOverview:
    cards = [item if hasattr(item, "risk_level") else build_cockpit_recommendation(item) for item in recommendations]
    return DashboardOverview(
        market=build_market_temperature(market_snapshot),
        recommendations=cards,
        risk_radar=build_risk_radar(cards),
        current_run=build_run_progress(recent_run),
        data_health=data_health,
        task_health=task_health,
        validation=validation,
    )


@router.get("/overview", response_model=DashboardOverview)
def overview(db: Session = Depends(get_db)) -> DashboardOverview:
    from app.services.data_source_health import active_alerts
    from app.services.data_sources import get_local_pool_status
    from app.services.workbench_service import bootstrap_workbench_state
    from app.api.admin import list_tasks

    try:
        home = bootstrap_workbench_state(db)
        recent_run = home.get("recent_runs", {}).get("formal") or home.get("recent_runs", {}).get("experiment")
        run_recs = []
        if recent_run and recent_run.get("run_id"):
            run_recs = list_run_recommendations(db, recent_run["run_id"])[:12]
        pool_status = get_local_pool_status(db, unified_limit=20, unified_offset=0, bars_limit=20, bars_offset=0)
        return compose_overview(
            market_snapshot=home.get("market_snapshot") or {},
            recent_run=recent_run,
            recommendations=run_recs,
            data_health=summarize_pool_status(pool_status),
            task_health=summarize_tasks(list_tasks(db)),
            validation={"last_status": "not_run", "alerts": summarize_alerts(active_alerts(db))},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

- [ ] **Step 4: Register router**

Modify `backend/app/main.py` import line:

```python
from app.api import admin, dashboard, data_sources, frontend_logs, home, metrics, outcomes, presets, recommendations, runs, skills, stats
```

Add router registration before `frontend_logs`:

```python
app.include_router(dashboard.router)
```

- [ ] **Step 5: Run dashboard tests**

Run:

```bash
cd backend && ./.venv/bin/python -m pytest -q tests/test_dashboard_projection.py
```

Expected: pass.

- [ ] **Step 6: Run focused import check**

Run:

```bash
cd backend && ./.venv/bin/python - <<'PY'
from app.main import app
paths = sorted(route.path for route in app.routes)
assert "/api/dashboard/overview" in paths
print("dashboard route registered")
PY
```

Expected: prints `dashboard route registered`.

- [ ] **Step 7: Stage and commit**

Run:

```bash
git add backend/app/api/dashboard.py backend/app/main.py backend/tests/test_dashboard_projection.py
git commit -m "feat: expose cockpit dashboard api"
```

---

## Task 5: Add Safety Guards to Validation Scripts

**Files:**
- Create: `backend/app/services/validation/__init__.py`
- Create: `backend/app/services/validation/safety.py`
- Modify: `backend/scripts/acceptance_check.py`
- Modify: `backend/scripts/rebuild_data_pool.py`
- Test: `backend/tests/test_validation_safety.py`

- [ ] **Step 1: Write safety tests**

Create `backend/tests/test_validation_safety.py` with:

```python
import os

import pytest

from app.services.validation.safety import (
    AcceptanceMode,
    assert_destructive_allowed,
    build_batch_id,
    tag_acceptance_name,
)


def test_build_batch_id_is_identifiable():
    batch_id = build_batch_id()
    assert batch_id.startswith("acceptance-")
    assert len(batch_id) >= len("acceptance-20260525-120000")


def test_tag_acceptance_name_contains_batch_id():
    tagged = tag_acceptance_name("驾驶舱验收", "acceptance-20260525-120000")
    assert tagged == "驾驶舱验收 [acceptance-20260525-120000]"


def test_acceptance_mode_requires_execute_for_writes():
    mode = AcceptanceMode(dry_run=True, execute=False)
    assert mode.can_write is False
    mode = AcceptanceMode(dry_run=False, execute=True)
    assert mode.can_write is True


def test_destructive_guard_blocks_without_double_confirm(monkeypatch):
    monkeypatch.delenv("ASHARE_ALLOW_PRODUCTION_DESTRUCTIVE", raising=False)
    with pytest.raises(RuntimeError, match="destructive operation blocked"):
        assert_destructive_allowed(database_url="mysql+pymysql://user@prod/ashare_workbench", requested=True)


def test_destructive_guard_allows_non_production_when_requested(monkeypatch):
    monkeypatch.delenv("ASHARE_ALLOW_PRODUCTION_DESTRUCTIVE", raising=False)
    assert_destructive_allowed(database_url="mysql+pymysql://user@127.0.0.1/ashare_test", requested=True)
```

- [ ] **Step 2: Run safety tests and verify they fail**

Run:

```bash
cd backend && ./.venv/bin/python -m pytest -q tests/test_validation_safety.py
```

Expected: fail because `app.services.validation.safety` does not exist.

- [ ] **Step 3: Implement safety service**

Create `backend/app/services/validation/__init__.py` as an empty package marker.

Create `backend/app/services/validation/safety.py` with:

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AcceptanceMode:
    dry_run: bool = True
    execute: bool = False

    @property
    def can_write(self) -> bool:
        return bool(self.execute and not self.dry_run)


def build_batch_id(now: datetime | None = None) -> str:
    current = now or datetime.now()
    return f"acceptance-{current.strftime('%Y%m%d-%H%M%S')}"


def tag_acceptance_name(name: str, batch_id: str) -> str:
    base = str(name).strip() or "验收对象"
    return f"{base} [{batch_id}]"


def _is_production_database(database_url: str) -> bool:
    lowered = str(database_url or "").lower()
    if "ashare_test" in lowered or "127.0.0.1" in lowered or "localhost" in lowered:
        return False
    return "ashare_workbench" in lowered or "mysql" in lowered


def assert_destructive_allowed(*, database_url: str, requested: bool) -> None:
    if not requested:
        raise RuntimeError("destructive operation blocked: explicit flag is required")
    if _is_production_database(database_url) and os.environ.get("ASHARE_ALLOW_PRODUCTION_DESTRUCTIVE") != "1":
        raise RuntimeError("destructive operation blocked: production database requires ASHARE_ALLOW_PRODUCTION_DESTRUCTIVE=1")
```

- [ ] **Step 4: Add acceptance script CLI flags**

Modify `backend/scripts/acceptance_check.py`:

1. Add imports near the top:

```python
import argparse
from app.services.validation.safety import AcceptanceMode, build_batch_id, tag_acceptance_name
```

2. Replace the constant-only base URL flow with parsed args:

```python
DEFAULT_BASE_URL = "http://127.0.0.1:8765"
BASE_URL = DEFAULT_BASE_URL


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AShare safe acceptance check")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--execute", action="store_true", default=False)
    parser.add_argument("--run-mode", choices=["formal", "experiment"], default="experiment")
    parser.add_argument("--batch-id", default="")
    parser.add_argument("--cleanup-batch", default="")
    return parser.parse_args()
```

3. At the start of `main()`, parse args and set globals:

```python
def main() -> int:
    global BASE_URL
    args = parse_args()
    BASE_URL = args.base_url.rstrip("/")
    mode = AcceptanceMode(dry_run=bool(args.dry_run or not args.execute), execute=bool(args.execute))
    batch_id = args.batch_id or build_batch_id()
    if args.cleanup_batch:
        print(json.dumps({"cleanup_batch": args.cleanup_batch, "status": "manual_cleanup_required"}, ensure_ascii=False))
        return 0
```

4. Change acceptance preset creation to use batch tag and run mode:

```python
preset_payload = {
    "name": tag_acceptance_name("验收自动预设", batch_id),
    "description": tag_acceptance_name("自动验收创建", batch_id),
    "run_mode": args.run_mode,
    "market_scope": "industry",
    "market_scope_value": "电气设备",
    "risk_template": "balanced",
    "output_count": 2,
    "data_source": "local_pool",
    "hide_recent_repeats": False,
    "skills": [{"skill_id": skill_id, "weight": 1.0, "sort_order": 0}],
}
```

5. Before any write request, guard with:

```python
if not mode.can_write:
    artifacts["dry_run"] = True
    print(json.dumps({"checks": [c.__dict__ for c in checks], "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0
```

- [ ] **Step 5: Add destructive guard to rebuild script**

Modify `backend/scripts/rebuild_data_pool.py`:

1. Import guard:

```python
from app.services.validation.safety import assert_destructive_allowed
```

2. Add CLI argument in the parser:

```python
parser.add_argument("--dangerously-clear-data-pool", action="store_true", help="Explicitly clear data-pool tables before rebuild")
```

3. Replace unconditional `clear_all_tables(db)` call in `main()` with:

```python
if args.dangerously_clear_data_pool:
    assert_destructive_allowed(database_url=settings.database_url, requested=True)
    clear_all_tables(db)
else:
    logger.info("跳过清空数据池；如需清空请显式传 --dangerously-clear-data-pool")
```

- [ ] **Step 6: Run safety tests**

Run:

```bash
cd backend && ./.venv/bin/python -m pytest -q tests/test_validation_safety.py
```

Expected: pass.

- [ ] **Step 7: Run acceptance dry-run smoke**

Run:

```bash
cd backend && ./.venv/bin/python scripts/acceptance_check.py --base-url http://127.0.0.1:8765 --dry-run
```

Expected: exits without creating data. If local backend is not running, output may report health failure but must not attempt writes.

- [ ] **Step 8: Stage and commit**

Run:

```bash
git add backend/app/services/validation backend/scripts/acceptance_check.py backend/scripts/rebuild_data_pool.py backend/tests/test_validation_safety.py
git commit -m "feat: guard cockpit validation scripts"
```

---

## Task 6: Extract Low-Risk Run Service Boundaries

**Files:**
- Create: `backend/app/services/runs/__init__.py`
- Create: `backend/app/services/runs/progress.py`
- Create: `backend/app/services/runs/queue.py`
- Create: `backend/app/services/candidates/__init__.py`
- Create: `backend/app/services/candidates/trace.py`
- Modify: `backend/app/services/workbench_service.py`
- Test: `backend/tests/test_dashboard_projection.py`

- [ ] **Step 1: Add tests for progress and trace helpers**

Append to `backend/tests/test_dashboard_projection.py`:

```python
from app.services.candidates.trace import candidate_trace_row
from app.services.runs.progress import is_resumable_status, progress_pct


def test_progress_helpers_are_stable():
    assert progress_pct(5, 10) == 50.0
    assert progress_pct(0, 0) is None
    assert is_resumable_status("interrupted") is True
    assert is_resumable_status("completed") is False


def test_candidate_trace_row_normalizes_metrics():
    row = candidate_trace_row(
        code="000001",
        stage="skill_gate",
        passed=True,
        reason="规则通过",
        metrics={"amount_yi": 8.2},
    )

    assert row["code"] == "000001"
    assert row["metrics"]["amount_yi"] == 8.2
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
cd backend && ./.venv/bin/python -m pytest -q tests/test_dashboard_projection.py
```

Expected: fail because `app.services.runs` and `app.services.candidates` do not exist.

- [ ] **Step 3: Implement helper modules**

Create package markers:

```bash
touch backend/app/services/runs/__init__.py backend/app/services/candidates/__init__.py
```

Create `backend/app/services/runs/progress.py`:

```python
from __future__ import annotations


RESUMABLE_STATUSES = {"interrupted", "failed"}


def progress_pct(current: int | None, total: int | None) -> float | None:
    current_value = int(current or 0)
    total_value = int(total or 0)
    if total_value <= 0:
        return None
    return round(min(100.0, max(0.0, current_value * 100 / total_value)), 2)


def is_resumable_status(status: str | None) -> bool:
    return str(status or "") in RESUMABLE_STATUSES
```

Create `backend/app/services/candidates/trace.py`:

```python
from __future__ import annotations

from typing import Any


def candidate_trace_row(*, code: str, stage: str, passed: bool, reason: str = "", metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "code": str(code),
        "stage": str(stage),
        "passed": bool(passed),
        "reason": str(reason or ""),
        "metrics": dict(metrics or {}),
    }
```

- [ ] **Step 4: Wire progress helper into workbench serialization**

Modify `_serialize_run` in `backend/app/services/workbench_service.py` to import and use:

```python
from app.services.runs.progress import is_resumable_status, progress_pct as compute_progress_pct
```

Replace local progress percent calculation with:

```python
progress_pct = compute_progress_pct(progress_current, progress_total)
```

Keep response keys unchanged.

- [ ] **Step 5: Run focused tests**

Run:

```bash
cd backend && ./.venv/bin/python -m pytest -q tests/test_dashboard_projection.py tests/test_e2e_recommendation_flow.py
```

Expected: dashboard tests pass; existing recommendation flow tests preserve current behavior.

- [ ] **Step 6: Stage and commit**

Run:

```bash
git add backend/app/services/runs backend/app/services/candidates backend/app/services/workbench_service.py backend/tests/test_dashboard_projection.py
git commit -m "refactor: extract run progress helpers"
```

---

## Task 7: Add Frontend Dashboard API Types

**Files:**
- Modify: `frontend/src/types.ts`
- Create: `frontend/src/api/dashboard.ts`
- Modify: `frontend/src/api/client.ts`

- [ ] **Step 1: Add TypeScript DTOs**

Append to `frontend/src/types.ts`:

```typescript
export type DataQualitySummary = {
  quote_status: string;
  quote_source_count: number;
  kline_status: string;
  kline_days: number;
  auxiliary_coverage: number;
  missing_fields: string[];
};

export type MarketTemperature = {
  trade_date: string;
  sample_count: number;
  up_count: number;
  down_count: number;
  limit_up_count: number;
  limit_down_count: number;
  up_ratio: number;
  heat_score: number;
  regime: string;
  main_industries: string[];
};

export type CockpitRecommendation = {
  recommendation_id: string;
  run_id: string;
  trade_date: string;
  code: string;
  name: string;
  industry?: string | null;
  final_score: number;
  score_gap?: number | null;
  bucket: string;
  setup_type?: string | null;
  rationale: string;
  risk_level: "low" | "medium" | "high" | "unknown";
  risk_tags: string[];
  info_tags: string[];
  status: string;
  watch_status: string;
  consensus_label: string;
  data_quality: DataQualitySummary;
};

export type RiskRadar = {
  high: number;
  medium: number;
  info: number;
  tags: string[];
};

export type RunProgress = {
  run_id?: string | null;
  status: string;
  phase?: string | null;
  progress_pct?: number | null;
  message: string;
  heartbeat_stale: boolean;
  resumable: boolean;
};

export type DashboardOverview = {
  market: MarketTemperature;
  recommendations: CockpitRecommendation[];
  risk_radar: RiskRadar;
  current_run: RunProgress;
  data_health: Record<string, unknown>;
  task_health: Record<string, unknown>;
  validation: Record<string, unknown>;
};
```

- [ ] **Step 2: Add dashboard API adapter**

Create `frontend/src/api/dashboard.ts`:

```typescript
import type { DashboardOverview } from "../types";

export type DashboardApi = {
  overview: () => Promise<DashboardOverview>;
};

export function createDashboardApi(request: <T>(path: string, init?: RequestInit) => Promise<T>): DashboardApi {
  return {
    overview: () => request<DashboardOverview>("/api/dashboard/overview")
  };
}
```

- [ ] **Step 3: Export dashboard adapter from client**

Modify `frontend/src/api/client.ts`:

1. Import factory:

```typescript
import { createDashboardApi } from "./dashboard";
```

2. Add to `api` object:

```typescript
dashboard: createDashboardApi(request),
```

- [ ] **Step 4: Run TypeScript build**

Run:

```bash
cd frontend && npm run build
```

Expected: build succeeds.

- [ ] **Step 5: Stage and commit**

Run:

```bash
git add frontend/src/types.ts frontend/src/api/dashboard.ts frontend/src/api/client.ts
git commit -m "feat: add frontend dashboard api contract"
```

---

## Task 8: Add Theme System and Thin App Shell

**Files:**
- Create: `frontend/src/shared/theme/initTheme.ts`
- Create: `frontend/src/shared/theme/useThemePreference.ts`
- Create: `frontend/src/app/nav.ts`
- Create: `frontend/src/app/AuthGate.tsx`
- Create: `frontend/src/app/AppShell.tsx`
- Replace: `frontend/src/App.tsx`
- Modify: `frontend/src/main.tsx`
- Create: `frontend/src/styles/tokens.css`
- Create: `frontend/src/styles/themes.css`
- Create: `frontend/src/styles/cockpit.css`
- Replace: `frontend/src/styles/app.css`

- [ ] **Step 1: Create theme initializer**

Create `frontend/src/shared/theme/initTheme.ts`:

```typescript
export type ThemePreference = "dark" | "light";

export const THEME_STORAGE_KEY = "ashare-theme";

export function resolveInitialTheme(): ThemePreference {
  const stored = localStorage.getItem(THEME_STORAGE_KEY);
  if (stored === "dark" || stored === "light") return stored;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function applyTheme(theme: ThemePreference) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(THEME_STORAGE_KEY, theme);
}
```

- [ ] **Step 2: Create React theme hook**

Create `frontend/src/shared/theme/useThemePreference.ts`:

```typescript
import { useCallback, useEffect, useState } from "react";
import { applyTheme, resolveInitialTheme, type ThemePreference } from "./initTheme";

export function useThemePreference() {
  const [theme, setTheme] = useState<ThemePreference>(() => resolveInitialTheme());

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme((current) => (current === "dark" ? "light" : "dark"));
  }, []);

  return { theme, setTheme, toggleTheme };
}
```

- [ ] **Step 3: Initialize theme before render**

Modify `frontend/src/main.tsx`:

```typescript
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles/app.css";
import { applyTheme, resolveInitialTheme } from "./shared/theme/initTheme";
import { startLongTaskMonitor } from "./utils/logger";

applyTheme(resolveInitialTheme());
startLongTaskMonitor(100);

createRoot(document.getElementById("root")!).render(<App />);
```

- [ ] **Step 4: Create navigation config**

Create `frontend/src/app/nav.ts`:

```typescript
import { Activity, BarChart3, DatabaseZap, History, Radar, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";

export type CockpitSection = "dashboard" | "run" | "pool" | "data" | "history" | "validation";

export type NavItem = {
  section: CockpitSection;
  label: string;
  subtitle: string;
  icon: ReactNode;
};

export const NAV_ITEMS: NavItem[] = [
  { section: "dashboard", label: "驾驶舱", subtitle: "市场温度 / 龙头候选 / 风险雷达", icon: <Radar size={17} /> },
  { section: "run", label: "推荐运行", subtitle: "预设 / 阶段 / 心跳", icon: <Activity size={17} /> },
  { section: "pool", label: "候选解释", subtitle: "推荐卡 / 个股解释 / 复盘", icon: <BarChart3 size={17} /> },
  { section: "data", label: "数据健康", subtitle: "数据池 / 数据源 / 调度", icon: <DatabaseZap size={17} /> },
  { section: "history", label: "复盘结果", subtitle: "历史 / 结果 / 统计", icon: <History size={17} /> },
  { section: "validation", label: "验收安全", subtitle: "dry-run / 批次 / 部署门禁", icon: <ShieldCheck size={17} /> },
];
```

- [ ] **Step 5: Create AuthGate**

Create `frontend/src/app/AuthGate.tsx`:

```tsx
import { Loader2, Play } from "lucide-react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api } from "../api/client";

type Props = { children: ReactNode };

export default function AuthGate({ children }: Props) {
  const [ready, setReady] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    void api.authStatus()
      .then((status) => setAuthenticated(!status.enabled || status.authenticated))
      .catch(() => setError("无法确认访问状态"))
      .finally(() => setReady(true));
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      await api.login(password);
      setAuthenticated(true);
    } catch {
      setError("访问密码不正确");
    } finally {
      setLoading(false);
    }
  }

  if (!ready) {
    return <div className="auth-screen"><div className="auth-panel"><Loader2 className="spin" size={22} />正在进入驾驶舱</div></div>;
  }

  if (!authenticated) {
    return (
      <div className="auth-screen">
        <form className="auth-panel" onSubmit={submit}>
          <div className="auth-title">短线交易驾驶舱</div>
          <input className="auth-input" type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
          {error ? <div className="auth-error">{error}</div> : null}
          <button className="primary auth-submit" type="submit" disabled={loading || !password}>
            {loading ? <Loader2 className="spin" size={16} /> : <Play size={16} />}
            进入
          </button>
        </form>
      </div>
    );
  }

  return <>{children}</>;
}
```

- [ ] **Step 6: Create AppShell**

Create `frontend/src/app/AppShell.tsx`:

```tsx
import { RefreshCcw, SunMoon } from "lucide-react";
import { useState } from "react";
import DashboardPage from "../features/dashboard/DashboardPage";
import { useThemePreference } from "../shared/theme/useThemePreference";
import { NAV_ITEMS, type CockpitSection } from "./nav";

export default function AppShell() {
  const [section, setSection] = useState<CockpitSection>("dashboard");
  const { theme, toggleTheme } = useThemePreference();
  const active = NAV_ITEMS.find((item) => item.section === section) ?? NAV_ITEMS[0];

  return (
    <div className="cockpit-shell">
      <aside className="cockpit-sidebar">
        <div className="cockpit-brand">
          <div className="brand-mark">A</div>
          <div><strong>短线交易驾驶舱</strong><span>真实数据 · 可解释推荐</span></div>
        </div>
        <nav className="cockpit-nav">
          {NAV_ITEMS.map((item) => (
            <button key={item.section} className={item.section === section ? "active" : ""} onClick={() => setSection(item.section)}>
              {item.icon}<span>{item.label}</span>
            </button>
          ))}
        </nav>
      </aside>
      <main className="cockpit-main">
        <header className="cockpit-topbar">
          <div><h1>{active.label}</h1><p>{active.subtitle}</p></div>
          <div className="topbar-actions">
            <button type="button" onClick={() => window.location.reload()}><RefreshCcw size={15} />刷新</button>
            <button type="button" onClick={toggleTheme}><SunMoon size={15} />{theme === "dark" ? "黑色" : "白色"}</button>
          </div>
        </header>
        {section === "dashboard" ? <DashboardPage /> : <DashboardPage compactMode={section} />}
      </main>
    </div>
  );
}
```

- [ ] **Step 7: Replace App entry**

Replace `frontend/src/App.tsx` with:

```tsx
import AppShell from "./app/AppShell";
import AuthGate from "./app/AuthGate";

export default function App() {
  return (
    <AuthGate>
      <AppShell />
    </AuthGate>
  );
}
```

- [ ] **Step 8: Add cockpit CSS tokens**

Create `frontend/src/styles/tokens.css`:

```css
:root {
  --radius-sm: 6px;
  --radius-md: 8px;
  --font-ui: Inter, "PingFang SC", "Microsoft YaHei", Arial, sans-serif;
  --motion-fast: 160ms ease;
}
```

Create `frontend/src/styles/themes.css`:

```css
:root[data-theme="dark"] {
  --canvas: #070b0f;
  --surface: #0e151b;
  --surface-soft: #131d25;
  --line: #23303a;
  --ink: #eef6f8;
  --muted: #8fa1ad;
  --accent: #2dd4bf;
  --danger: #f97373;
  --warning: #f4b85e;
  --positive: #3ddc97;
  --negative: #ff6b6b;
}

:root[data-theme="light"] {
  --canvas: #f4f7f9;
  --surface: #ffffff;
  --surface-soft: #eef3f6;
  --line: #d8e1e7;
  --ink: #11181c;
  --muted: #667782;
  --accent: #087f7a;
  --danger: #c93636;
  --warning: #a66b00;
  --positive: #087f5b;
  --negative: #c93636;
}
```

Create `frontend/src/styles/cockpit.css` with shell and panel classes:

```css
* { box-sizing: border-box; }
html, body, #root { min-height: 100%; margin: 0; }
body { margin: 0; background: var(--canvas); color: var(--ink); font-family: var(--font-ui); }
button, input, select, textarea { font: inherit; }
button { cursor: pointer; }
.cockpit-shell { min-height: 100vh; display: grid; grid-template-columns: 232px minmax(0, 1fr); background: var(--canvas); }
.cockpit-sidebar { border-right: 1px solid var(--line); background: var(--surface); padding: 18px 12px; display: flex; flex-direction: column; gap: 18px; }
.cockpit-brand { display: flex; gap: 10px; align-items: center; padding: 4px 6px 10px; }
.brand-mark { width: 34px; height: 34px; border-radius: var(--radius-md); background: var(--accent); color: var(--canvas); display: grid; place-items: center; font-weight: 800; }
.cockpit-brand strong, .cockpit-brand span { display: block; }
.cockpit-brand span { margin-top: 3px; color: var(--muted); font-size: 12px; }
.cockpit-nav { display: grid; gap: 6px; }
.cockpit-nav button, .topbar-actions button, .primary { border: 1px solid var(--line); border-radius: var(--radius-md); background: transparent; color: var(--ink); min-height: 38px; display: inline-flex; align-items: center; gap: 8px; padding: 8px 10px; }
.cockpit-nav button.active, .cockpit-nav button:hover, .topbar-actions button:hover { border-color: var(--accent); background: var(--surface-soft); }
.cockpit-main { min-width: 0; padding: 18px; }
.cockpit-topbar { display: flex; justify-content: space-between; gap: 16px; align-items: center; margin-bottom: 16px; }
.cockpit-topbar h1 { margin: 0; font-size: 24px; letter-spacing: 0; }
.cockpit-topbar p { margin: 5px 0 0; color: var(--muted); font-size: 13px; }
.topbar-actions { display: flex; gap: 8px; }
.auth-screen { min-height: 100vh; display: grid; place-items: center; background: var(--canvas); padding: 24px; }
.auth-panel { width: min(420px, 100%); border: 1px solid var(--line); border-radius: var(--radius-md); background: var(--surface); padding: 24px; display: grid; gap: 14px; }
.auth-title { font-weight: 800; font-size: 18px; }
.auth-input { min-height: 42px; border: 1px solid var(--line); border-radius: var(--radius-md); background: var(--surface-soft); color: var(--ink); padding: 8px 10px; }
.auth-error { color: var(--danger); font-size: 13px; }
.auth-submit { justify-content: center; background: var(--accent); color: var(--canvas); border-color: var(--accent); }
.spin { animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
```

Replace `frontend/src/styles/app.css` with:

```css
@import "./tokens.css";
@import "./themes.css";
@import "./cockpit.css";
```

- [ ] **Step 9: Run frontend build**

Run:

```bash
cd frontend && npm run build
```

Expected: initial failure because `DashboardPage` does not exist. Keep this failure for Task 9.

- [ ] **Step 10: Stage without commit**

Run:

```bash
git add frontend/src/App.tsx frontend/src/app frontend/src/shared/theme frontend/src/main.tsx frontend/src/styles
```

Commit after Task 9 creates the missing dashboard page and the build passes.

---

## Task 9: Build Cockpit Dashboard Page

**Files:**
- Create: `frontend/src/features/dashboard/DashboardPage.tsx`
- Create: `frontend/src/features/dashboard/hooks/useDashboardViewModel.ts`
- Create: `frontend/src/features/dashboard/components/MarketTemperaturePanel.tsx`
- Create: `frontend/src/features/dashboard/components/DragonCandidatesPanel.tsx`
- Create: `frontend/src/features/dashboard/components/RiskRadarPanel.tsx`
- Create: `frontend/src/features/dashboard/components/RunStatusPanel.tsx`
- Create: `frontend/src/features/dashboard/components/DataHealthPanel.tsx`
- Modify: `frontend/src/styles/cockpit.css`

- [ ] **Step 1: Create dashboard hook**

Create `frontend/src/features/dashboard/hooks/useDashboardViewModel.ts`:

```typescript
import { useCallback, useEffect, useState } from "react";
import { api } from "../../../api/client";
import type { DashboardOverview } from "../../../types";

export function useDashboardViewModel() {
  const [data, setData] = useState<DashboardOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const overview = await api.dashboard.overview();
      setData(overview);
    } catch (err) {
      setError(err instanceof Error ? err.message : "驾驶舱数据加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { data, loading, error, refresh };
}
```

- [ ] **Step 2: Create MarketTemperaturePanel**

Create `frontend/src/features/dashboard/components/MarketTemperaturePanel.tsx`:

```tsx
import type { MarketTemperature } from "../../../types";

export default function MarketTemperaturePanel({ market }: { market: MarketTemperature }) {
  return (
    <section className="cockpit-panel market-temperature">
      <div className="panel-kicker">今日市场温度</div>
      <div className="temperature-score">{market.heat_score.toFixed(1)}</div>
      <div className="metric-grid">
        <span>交易日<strong>{market.trade_date || "-"}</strong></span>
        <span>上涨<strong className="positive">{market.up_count}</strong></span>
        <span>下跌<strong className="negative">{market.down_count}</strong></span>
        <span>涨比<strong>{(market.up_ratio * 100).toFixed(1)}%</strong></span>
        <span>涨停<strong className="positive">{market.limit_up_count}</strong></span>
        <span>跌停<strong className="negative">{market.limit_down_count}</strong></span>
      </div>
      <div className="theme-strip">
        {market.main_industries.length ? market.main_industries.map((item) => <span key={item}>{item}</span>) : <span>暂无主线</span>}
      </div>
    </section>
  );
}
```

- [ ] **Step 3: Create DragonCandidatesPanel**

Create `frontend/src/features/dashboard/components/DragonCandidatesPanel.tsx`:

```tsx
import type { CockpitRecommendation } from "../../../types";

export default function DragonCandidatesPanel({ items }: { items: CockpitRecommendation[] }) {
  return (
    <section className="cockpit-panel dragon-candidates">
      <div className="panel-header"><div><div className="panel-kicker">龙头候选</div><h2>强度排序</h2></div><span>{items.length} 只</span></div>
      <div className="candidate-list">
        {items.length ? items.map((item) => (
          <article key={item.recommendation_id} className={`candidate-card risk-${item.risk_level}`}>
            <div className="candidate-main">
              <div><strong>{item.name}</strong><span>{item.code} · {item.industry || "未分类"}</span></div>
              <div className="score-pill">{item.final_score.toFixed(1)}</div>
            </div>
            <p>{item.rationale || "暂无解释"}</p>
            <div className="candidate-meta">
              <span>{item.setup_type || "未识别买点"}</span>
              <span>Gap {item.score_gap == null ? "-" : item.score_gap.toFixed(1)}</span>
              <span>{item.consensus_label}</span>
            </div>
          </article>
        )) : <div className="empty-state">暂无推荐结果</div>}
      </div>
    </section>
  );
}
```

- [ ] **Step 4: Create RiskRadarPanel**

Create `frontend/src/features/dashboard/components/RiskRadarPanel.tsx`:

```tsx
import type { RiskRadar } from "../../../types";

export default function RiskRadarPanel({ radar }: { radar: RiskRadar }) {
  return (
    <section className="cockpit-panel">
      <div className="panel-kicker">风险雷达</div>
      <div className="risk-grid">
        <span><strong className="negative">{radar.high}</strong>高风险</span>
        <span><strong>{radar.medium}</strong>中风险</span>
        <span><strong>{radar.info}</strong>信息提示</span>
      </div>
      <div className="tag-cloud">
        {radar.tags.length ? radar.tags.map((tag) => <span key={tag}>{tag}</span>) : <span>暂无显著风险</span>}
      </div>
    </section>
  );
}
```

- [ ] **Step 5: Create RunStatusPanel**

Create `frontend/src/features/dashboard/components/RunStatusPanel.tsx`:

```tsx
import type { RunProgress } from "../../../types";

export default function RunStatusPanel({ run }: { run: RunProgress }) {
  const pct = run.progress_pct == null ? 0 : run.progress_pct;
  return (
    <section className="cockpit-panel">
      <div className="panel-kicker">推荐运行状态</div>
      <div className="run-status-line"><strong>{run.status}</strong><span>{run.phase || "idle"}</span></div>
      <div className="progress-track"><span style={{ width: `${pct}%` }} /></div>
      <p>{run.message || (run.run_id ? "运行状态正常" : "暂无运行")}</p>
      {run.heartbeat_stale ? <span className="status-badge danger">心跳过期</span> : <span className="status-badge">心跳正常</span>}
      {run.resumable ? <span className="status-badge warning">可恢复</span> : null}
    </section>
  );
}
```

- [ ] **Step 6: Create DataHealthPanel**

Create `frontend/src/features/dashboard/components/DataHealthPanel.tsx`:

```tsx
export default function DataHealthPanel({ dataHealth, taskHealth }: { dataHealth: Record<string, unknown>; taskHealth: Record<string, unknown> }) {
  return (
    <section className="cockpit-panel">
      <div className="panel-kicker">数据池与调度</div>
      <div className="metric-grid">
        <span>状态<strong>{String(dataHealth.status || "unknown")}</strong></span>
        <span>报价<strong>{String(dataHealth.quote_total || 0)}</strong></span>
        <span>可选<strong>{String(dataHealth.eligible_quote_count || 0)}</strong></span>
        <span>日K覆盖<strong>{String(dataHealth.bar_coverage_pct || 0)}%</strong></span>
        <span>任务运行<strong>{String(taskHealth.running_count || 0)}</strong></span>
        <span>任务失败<strong>{String(taskHealth.failed_count || 0)}</strong></span>
      </div>
    </section>
  );
}
```

- [ ] **Step 7: Create DashboardPage**

Create `frontend/src/features/dashboard/DashboardPage.tsx`:

```tsx
import DataHealthPanel from "./components/DataHealthPanel";
import DragonCandidatesPanel from "./components/DragonCandidatesPanel";
import MarketTemperaturePanel from "./components/MarketTemperaturePanel";
import RiskRadarPanel from "./components/RiskRadarPanel";
import RunStatusPanel from "./components/RunStatusPanel";
import { useDashboardViewModel } from "./hooks/useDashboardViewModel";

export default function DashboardPage({ compactMode }: { compactMode?: string }) {
  const { data, loading, error, refresh } = useDashboardViewModel();

  if (loading) return <div className="cockpit-panel">正在加载驾驶舱数据</div>;
  if (error) return <div className="cockpit-panel error-state"><strong>驾驶舱加载失败</strong><span>{error}</span><button onClick={refresh}>重试</button></div>;
  if (!data) return <div className="cockpit-panel">暂无驾驶舱数据</div>;

  return (
    <div className={`dashboard-grid ${compactMode ? "compact" : ""}`}>
      <MarketTemperaturePanel market={data.market} />
      <DragonCandidatesPanel items={data.recommendations} />
      <RiskRadarPanel radar={data.risk_radar} />
      <RunStatusPanel run={data.current_run} />
      <DataHealthPanel dataHealth={data.data_health} taskHealth={data.task_health} />
    </div>
  );
}
```

- [ ] **Step 8: Add dashboard CSS**

Append to `frontend/src/styles/cockpit.css`:

```css
.dashboard-grid { display: grid; grid-template-columns: minmax(320px, 0.85fr) minmax(420px, 1.25fr) minmax(300px, 0.8fr); gap: 14px; align-items: start; }
.dashboard-grid.compact { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.cockpit-panel { border: 1px solid var(--line); border-radius: var(--radius-md); background: var(--surface); padding: 16px; min-width: 0; }
.panel-kicker { color: var(--muted); font-size: 12px; font-weight: 700; }
.panel-header { display: flex; justify-content: space-between; gap: 12px; align-items: center; }
.panel-header h2 { margin: 4px 0 0; font-size: 18px; }
.temperature-score { font-size: 56px; font-weight: 850; color: var(--accent); line-height: 1; margin: 14px 0; }
.metric-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; }
.metric-grid span { border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 9px; color: var(--muted); display: grid; gap: 4px; font-size: 12px; }
.metric-grid strong { color: var(--ink); font-size: 16px; }
.positive { color: var(--positive) !important; }
.negative { color: var(--negative) !important; }
.theme-strip, .tag-cloud, .candidate-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
.theme-strip span, .tag-cloud span, .candidate-meta span, .status-badge { border: 1px solid var(--line); border-radius: 999px; padding: 4px 8px; color: var(--muted); font-size: 12px; }
.candidate-list { display: grid; gap: 10px; margin-top: 12px; }
.candidate-card { border: 1px solid var(--line); border-radius: var(--radius-md); padding: 12px; background: var(--surface-soft); }
.candidate-main { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
.candidate-main strong, .candidate-main span { display: block; }
.candidate-main span { color: var(--muted); font-size: 12px; margin-top: 3px; }
.score-pill { border-radius: 999px; background: var(--accent); color: var(--canvas); padding: 4px 9px; font-weight: 800; }
.candidate-card p { color: var(--muted); font-size: 13px; line-height: 1.5; margin: 10px 0 0; }
.risk-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-top: 12px; }
.risk-grid span { border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 10px; color: var(--muted); display: grid; }
.risk-grid strong { font-size: 24px; color: var(--ink); }
.run-status-line { display: flex; justify-content: space-between; margin-top: 12px; }
.progress-track { height: 8px; border-radius: 999px; background: var(--surface-soft); overflow: hidden; margin: 12px 0; }
.progress-track span { display: block; height: 100%; background: var(--accent); transition: width var(--motion-fast); }
.status-badge.danger { color: var(--danger); border-color: var(--danger); }
.status-badge.warning { color: var(--warning); border-color: var(--warning); }
.empty-state, .error-state span { color: var(--muted); }
@media (max-width: 1120px) { .dashboard-grid, .dashboard-grid.compact { grid-template-columns: 1fr; } .cockpit-shell { grid-template-columns: 1fr; } .cockpit-sidebar { position: static; } }
```

- [ ] **Step 9: Run frontend build**

Run:

```bash
cd frontend && npm run build
```

Expected: build succeeds.

- [ ] **Step 10: Stage and commit shell plus dashboard**

Run:

```bash
git add frontend/src
git commit -m "feat: replace workbench with cockpit shell"
```

---

## Task 10: Update Documentation and Remove Stale Data-Pool Terms

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/上线验收标准.md`

- [ ] **Step 1: Replace stale `UnifiedQuote` table wording**

Run:

```bash
rg -n "UnifiedQuote|unified_quotes|daily_bars|daily_indicator|TickFlow|tickflow" README.md docs
```

For every occurrence that describes the current table model, replace it with the current model:

- `UnifiedQuote` and `unified_quotes` -> `StockQuote` / `stock_quotes`
- `daily_bars` and `daily_indicator` -> `DailyKbar` / `daily_kbars`
- `TickFlow` as required K-line source -> remove from required source list unless code support is added in the same task

- [ ] **Step 2: Add cockpit architecture summary**

In `README.md`, update the front-end bullet list to:

```markdown
- 前端默认进入短线交易驾驶舱，聚合市场温度、主线强度、龙头候选、风险雷达、推荐运行、数据池健康、调度和复盘反馈。
- 驾驶舱支持黑色/白色主题切换，主题选择保存在浏览器本地。
```

- [ ] **Step 3: Add dashboard API to API overview**

In `README.md` API table, add:

```markdown
| GET | `/api/dashboard/overview` | 驾驶舱首页聚合状态 |
```

- [ ] **Step 4: Update acceptance criteria**

In `docs/上线验收标准.md`, add a cockpit acceptance section:

```markdown
## 短线交易驾驶舱准入

| # | 验收项 | 验证方法 | 判定标准 |
|---|--------|----------|----------|
| C.1 | 首页默认进入驾驶舱 | 访问前端首页 | 首屏展示市场温度、龙头候选、风险雷达、数据池健康 |
| C.2 | 主题切换持久化 | 切换黑白主题后刷新 | 主题保持用户选择 |
| C.3 | 推荐解释结构化 | 打开推荐卡 | 展示分数、理由、风险、Skill 审查和数据质量 |
| C.4 | 安全验收 dry-run | 执行 `scripts/acceptance_check.py --dry-run` | 不写入业务数据 |
```

- [ ] **Step 5: Run docs grep verification**

Run:

```bash
rg -n "UnifiedQuote|unified_quotes|daily_indicator|TickFlow|tickflow" README.md docs || true
```

Expected: no stale term remains unless it is explicitly described as an old removed term.

- [ ] **Step 6: Stage and commit**

Run:

```bash
git add README.md docs/architecture.md docs/上线验收标准.md
git commit -m "docs: align cockpit architecture and data pool terms"
```

---

## Task 11: Full Verification

**Files:**
- No source edits unless verification finds a defect.

- [ ] **Step 1: Run backend projection and safety tests**

Run:

```bash
cd backend && ./.venv/bin/python -m pytest -q \
  tests/test_dashboard_projection.py \
  tests/test_data_pool_readiness_projection.py \
  tests/test_validation_safety.py
```

Expected: pass.

- [ ] **Step 2: Run backend core tests**

Run:

```bash
cd backend && ./.venv/bin/python -m pytest -q \
  tests/test_feature_builder.py \
  tests/test_aggregator.py \
  tests/test_hallucination_check.py \
  tests/test_research_context.py \
  tests/test_market_pool.py
```

Expected: pass. If DB-backed tests require `ASHARE_TEST_DATABASE_URL`, use safe-mode only when targeting a production-like database:

```bash
ASHARE_TEST_SAFE_MODE=1 ./.venv/bin/python -m pytest -q tests/test_hallucination_check.py tests/test_research_context.py
```

- [ ] **Step 3: Run frontend build**

Run:

```bash
cd frontend && npm run build
```

Expected: TypeScript and Vite build pass.

- [ ] **Step 4: Start local app**

Run:

```bash
./start.sh
```

Expected: backend on `http://localhost:8765`, frontend on `http://localhost:5173`.

- [ ] **Step 5: Verify HTTP endpoints**

In a second terminal:

```bash
curl -s http://localhost:8765/api/health
curl -s http://localhost:8765/api/dashboard/overview | python -m json.tool | head -60
```

Expected: health returns `{"ok": true}`. Dashboard endpoint returns `market`, `recommendations`, `risk_radar`, `current_run`, `data_health`, `task_health`, and `validation`.

- [ ] **Step 6: Run acceptance dry-run**

Run:

```bash
cd backend && ./.venv/bin/python scripts/acceptance_check.py --base-url http://127.0.0.1:8765 --dry-run
```

Expected: no business writes.

- [ ] **Step 7: Inspect git status**

Run:

```bash
git status --short
git log --oneline -8
```

Expected: clean status; commits from Tasks 1 through 10 appear.

---

## Task 12: Integration Review and Push

**Files:**
- No source edits unless review finds a defect.

- [ ] **Step 1: Review changed files**

Run:

```bash
git diff main...HEAD --stat
git diff main...HEAD -- backend/app/main.py backend/app/api/dashboard.py backend/scripts/rebuild_data_pool.py frontend/src/App.tsx frontend/src/styles/app.css | sed -n '1,260p'
```

Expected: changes match cockpit implementation, no sensitive credentials, no accidental production deployment commands.

- [ ] **Step 2: Confirm no cloud deploy**

Run:

```bash
rg -n "139\\.196\\.242\\.53|ashare-server|systemctl|nginx" .
```

Expected: matches may exist in existing docs, but no newly added script runs cloud deployment automatically.

- [ ] **Step 3: Push branch**

Run:

```bash
git push origin codex/shortline-trading-cockpit
```

Expected: branch pushed to GitHub. Do not deploy to the cloud server.

---

## Self-Review

- Spec coverage: Tasks cover frontend cockpit, theme persistence, dashboard API, backend projection, data-pool readiness, safety validation, docs, and verification.
- Ordering: Backend DTO/projection/API come before frontend API consumption. Theme shell is paired with dashboard page before committing to keep frontend build green.
- Type consistency: Python DTO names match TypeScript DTO names: `DashboardOverview`, `MarketTemperature`, `CockpitRecommendation`, `RiskRadar`, `RunProgress`, `DataQualitySummary`.
- Safety: Validation defaults to dry-run and destructive data-pool clearing requires explicit flags.
- Deployment: Plan explicitly avoids cloud deployment unless the user asks again.
