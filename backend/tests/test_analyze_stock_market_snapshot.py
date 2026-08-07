"""analyze-stock 市场快照解析：优先 formal run 全市场 snapshot。

T8（2026-07-21）：旧逻辑用单股最小 snapshot（sample_count=1, regime=未知），
LLM 系统性把主线票判 veto/冰点。修复后同 trade_date 的 formal snapshot 优先。
"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services import single_stock_analysis as analysis_service
from app.services.evidence import identity_from_persisted_run
from app.services.single_stock_analysis import _resolve_analysis_market_snapshot
from app.models.tables import (
    RecommendationReconstructionBatch,
    RecommendationRun,
)


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDB:
    def __init__(self, raw):
        self._raw = raw
        self.calls = 0

    def execute(self, *args, **kwargs):
        self.calls += 1
        return _FakeResult(self._raw)


def test_resolve_prefers_formal_run_snapshot(monkeypatch):
    snap = {
        "sample_count": 4954,
        "limit_up_count": 54,
        "limit_down_count": 77,
        "breadth": {"regime": "震荡", "up_ratio": 0.449, "hot_theme": "半导体"},
        "main_industries": ["半导体", "元器件"],
        "trade_date": "2026-07-16",
    }
    db = _FakeDB(json.dumps(snap, ensure_ascii=False))
    monkeypatch.setattr(
        analysis_service,
        "canonical_live_forward_run_ids",
        lambda _db, trade_date=None: ("run-live",),
    )
    monkeypatch.setattr(
        analysis_service,
        "canonical_historical_reconstruction_run_ids",
        lambda _db, trade_date=None: (),
    )
    out, source = _resolve_analysis_market_snapshot(
        db, "2026-07-16", {"code": "002241", "price": 10.0},
    )
    assert source == "formal_run_snapshot"
    assert out["sample_count"] == 4954
    assert out["breadth"]["regime"] == "震荡"
    assert out["limit_up_count"] == 54
    assert db.calls == 1


def test_resolve_falls_back_when_no_formal_snapshot(monkeypatch):
    db = _FakeDB(None)
    monkeypatch.setattr(
        analysis_service,
        "canonical_live_forward_run_ids",
        lambda _db, trade_date=None: (),
    )
    monkeypatch.setattr(
        analysis_service,
        "canonical_historical_reconstruction_run_ids",
        lambda _db, trade_date=None: (),
    )
    out, source = _resolve_analysis_market_snapshot(
        db, "2026-07-16", {
            "code": "002241", "name": "t", "price": 10.0, "pct": 1.0,
            "volume": 1, "amount_yi": 1, "turnover_rate": 1,
            "trade_date": "2026-07-16",
        },
    )
    assert source == "minimal_fallback"
    assert out["breadth"]["regime"] == "未知"
    assert int(out.get("sample_count") or 0) <= 1


def test_resolve_rejects_tiny_formal_snapshot(monkeypatch):
    """sample_count < 100 的残缺 formal snapshot 不采用，回退 minimal。"""
    snap = {"sample_count": 3, "breadth": {"regime": "震荡"}}
    db = _FakeDB(json.dumps(snap))
    monkeypatch.setattr(
        analysis_service,
        "canonical_live_forward_run_ids",
        lambda _db, trade_date=None: ("run-live",),
    )
    monkeypatch.setattr(
        analysis_service,
        "canonical_historical_reconstruction_run_ids",
        lambda _db, trade_date=None: (),
    )
    out, source = _resolve_analysis_market_snapshot(
        db, "2026-07-16", {
            "code": "002241", "name": "t", "price": 10.0, "pct": 1.0,
            "volume": 1, "amount_yi": 1, "turnover_rate": 1,
            "trade_date": "2026-07-16",
        },
    )
    assert source == "minimal_fallback"
    assert out["breadth"]["regime"] == "未知"


def test_resolve_snapshot_sql_prefers_canonical_live_over_replay_and_forged():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RecommendationRun.__table__.create(engine)
    RecommendationReconstructionBatch.__table__.create(engine)
    canonical_snapshot = {
        "sample_count": 500,
        "breadth": {"regime": "偏多"},
        "trade_date": "2099-03-01",
    }
    replay_snapshot = {
        "sample_count": 800,
        "breadth": {"regime": "历史回放"},
        "trade_date": "2099-03-01",
    }
    forged_snapshot = {
        "sample_count": 999,
        "breadth": {"regime": "伪造"},
        "trade_date": "2099-03-01",
    }
    canonical = RecommendationRun(
        run_id="single_snapshot_canonical",
        run_name="canonical snapshot",
        run_mode="formal",
        evaluation_scope="live_forward",
        trade_date="2099-03-01",
        status="completed",
        evidence_class="live_forward",
        data_as_of="2099-03-01",
        source_snapshot_id="single-snapshot",
        actual_generated_at=datetime(2099, 3, 1, 6, 0),
        evidence_identity_hash="d" * 64,
        strategy_policy_fingerprint="a" * 64,
        execution_policy_fingerprint="b" * 64,
        code_revision="c" * 40,
        provenance_schema_version=1,
        market_snapshot_json=json.dumps(canonical_snapshot),
        started_at=datetime(2099, 3, 1, 6, 0),
    )
    canonical.evidence_identity_hash = identity_from_persisted_run(
        canonical
    ).identity_hash
    replay = RecommendationRun(
        run_id="single_snapshot_replay",
        run_name="canonical replay snapshot",
        run_mode="formal",
        evaluation_scope="historical_replay",
        trade_date="2099-03-01",
        status="completed",
        evidence_class="historical_reconstruction",
        data_as_of="2099-03-01",
        source_snapshot_id="single-replay-snapshot",
        actual_generated_at=datetime(2099, 3, 2, 6, 0),
        evidence_identity_hash="e" * 64,
        strategy_policy_fingerprint="a" * 64,
        execution_policy_fingerprint="b" * 64,
        code_revision="c" * 40,
        reconstruction_batch_id="single-replay-batch",
        provenance_schema_version=1,
        market_snapshot_json=json.dumps(replay_snapshot),
        started_at=datetime(2099, 3, 2, 6, 0),
    )
    replay.evidence_identity_hash = identity_from_persisted_run(replay).identity_hash
    forged = RecommendationRun(
        run_id="single_snapshot_forged",
        run_name="forged snapshot",
        run_mode="formal",
        trade_date="2099-03-01",
        status="completed",
        evidence_class="live_forward",
        data_as_of="2099-03-01",
        source_snapshot_id="single-forged-snapshot",
        actual_generated_at=datetime(2099, 3, 1, 7, 0),
        evidence_identity_hash="0" * 64,
        strategy_policy_fingerprint="a" * 64,
        execution_policy_fingerprint="b" * 64,
        code_revision="c" * 40,
        provenance_schema_version=1,
        market_snapshot_json=json.dumps(forged_snapshot),
        started_at=datetime(2099, 3, 2, 7, 0),
    )
    with Session(engine) as db:
        db.add_all([canonical, replay, forged])
        db.commit()

        snapshot, source = _resolve_analysis_market_snapshot(
            db,
            "2099-03-01",
            {"code": "002241", "price": 10.0},
        )

    assert source == "formal_run_snapshot"
    assert snapshot["sample_count"] == 500
    assert snapshot["breadth"]["regime"] == "偏多"
