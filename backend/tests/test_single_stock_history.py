"""单股票评分历史落库（_persist_single_stock_history）单元测试。

验证 analyze_stock 返回前会把结果写入 stock_matrix_history 表，
analysis_type="single_stock" 与矩阵分析（"analysis"）隔离；
落库失败不抛出、不阻塞主流程。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.single_stock_analysis import _persist_single_stock_history


class _FakeDB:
    """记录 add/commit/rollback 调用的假 session。"""
    def __init__(self):
        self.added: list = []
        self.committed = False
        self.rolled_back = False

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _make_result(*, final_scores=None, elapsed_ms=120.0, llm=None):
    scoring = {
        "dsa_signal_score": 10.0,
        "dsa_trend_score": 8.0,
        "dsa_bias_score": 6.0,
        "dsa_volume_score": 5.0,
        "dsa_support_score": 4.0,
        "dsa_macd_score": 3.0,
        "dsa_rsi_score": 2.0,
        "risk_score": 1.0,
    }
    if final_scores is not None:
        scoring.update(final_scores)
    result = {
        "status": "ok",
        "code": "000001",
        "name": "平安银行",
        "trade_date": "2026-07-16",
        "bar_count": 120,
        "elapsed_ms": elapsed_ms,
        "scoring": scoring,
    }
    if llm is not None:
        result["llm_analysis"] = llm
    return result


def test_persist_writes_single_stock_history():
    """规则评分模式：final_score 使用已归一化的信号总分，不重复累加子分。"""
    db = _FakeDB()
    result = _make_result()

    _persist_single_stock_history(db, "000001", "2026-07-16", result)

    assert len(db.added) == 1
    assert db.committed is True
    assert db.rolled_back is False

    row = db.added[0]
    assert row.code == "000001"
    assert row.trade_date == "2026-07-16"
    assert row.analysis_type == "single_stock"
    assert row.final_score == pytest.approx(10.0)
    assert row.skill_count == 1
    assert row.elapsed_ms == 120.0
    assert '"status": "ok"' in row.result_json
    assert '"code": "000001"' in row.result_json


def test_preset_mode_skill_count_from_skill_reviews():
    """预设多 skill 模式：历史评分与推荐管线聚合最终分一致。"""
    db = _FakeDB()
    llm = {
        "status": "ok",
        "preset_id": "preset_formal_opening",
        "final_score": 72.5,
        "skill_reviews": [
            {"skill_id": "a", "status": "ok"},
            {"skill_id": "b", "status": "ok"},
            {"skill_id": "c", "status": "error"},
        ],
    }
    result = _make_result(llm=llm)

    _persist_single_stock_history(db, "002241", "2026-07-16", result)

    row = db.added[0]
    assert row.final_score == pytest.approx(72.5)
    assert row.skill_count == 2  # 两个 ok
    assert row.analysis_type == "single_stock"


def test_persist_rule_score_never_exceeds_100_by_readding_components():
    """回归：dsa_signal_score 已是六个子项归一化总分，不能再相加成 158。"""
    db = _FakeDB()
    result = _make_result(final_scores={
        "dsa_signal_score": 79.0,
        "dsa_trend_score": 26.0,
        "dsa_bias_score": 20.0,
        "dsa_volume_score": 10.0,
        "dsa_support_score": 10.0,
        "dsa_macd_score": 5.0,
        "dsa_rsi_score": 8.0,
    })

    _persist_single_stock_history(db, "000001", "2026-07-16", result)

    assert db.added[0].final_score == pytest.approx(79.0)
    assert 0 <= db.added[0].final_score <= 100


def test_pipeline_rejection_persists_no_score_instead_of_dsa_fallback():
    """正式管线未产出评分卡时，不能拿 DSA 特征分冒充最终推荐分。"""
    db = _FakeDB()
    result = _make_result()
    result["final_score"] = None

    _persist_single_stock_history(db, "000001", "2026-07-16", result)

    assert db.added[0].final_score is None


def test_persist_swallows_failure_without_raising():
    """落库异常不影响主流程：commit 抛错时仅 rollback，不向外抛。"""
    class _BoomDB(_FakeDB):
        def commit(self):
            raise RuntimeError("db down")

    db = _BoomDB()
    result = _make_result()

    # 不应抛出
    _persist_single_stock_history(db, "000001", "2026-07-16", result)

    assert len(db.added) == 1
    assert db.committed is False
    assert db.rolled_back is True
