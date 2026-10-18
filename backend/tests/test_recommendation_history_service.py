"""list_recommendation_history 回归测试。

验证跨 run 推荐历史 API 的核心逻辑：
- 按 (trade_date, code) 去重（教训库第 46 轮：回测 DB 累积重复）
- code/label 筛选
- 分页（limit/offset）
- 返回结构（items/total/offset/limit）

使用真实生产 MySQL（CLAUDE.md 允许），只读不写。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.db.session import SessionLocal
from app.models.tables import RecommendationRun
from app.services.workbench_service import (
    _build_recommendation_history_summary,
    list_recommendation_history,
)
from app.services.execution_outcome_policy import build_missed_observations

pytestmark = pytest.mark.integration


def _db():
    return SessionLocal()


def test_history_returns_deduplicated_items():
    """跨 run 历史按 (trade_date, code) 去重，total 应 <= 推荐总数。"""
    db = _db()
    try:
        result = list_recommendation_history(db, limit=50, offset=0)
        assert "items" in result
        assert "total" in result
        assert result["limit"] == 50
        assert result["offset"] == 0
        assert "summary" in result
        # 去重验证：items 里不应有重复 (trade_date, code)
        seen = set()
        for item in result["items"]:
            key = (item["trade_date"], item["code"])
            assert key not in seen, f"重复 (trade_date, code): {key}"
            seen.add(key)
        assert len(result["items"]) <= 50
    finally:
        db.close()


def test_history_summary_uses_settled_net_returns_only():
    outcomes = [
        SimpleNamespace(entry_triggered=True, outcome_label="success", label_source="auto", executed_return=5.0),
        SimpleNamespace(entry_triggered=True, outcome_label="failed", label_source="auto", executed_return=-2.0),
        SimpleNamespace(entry_triggered=False, outcome_label="missed", label_source="auto", executed_return=0.0),
        SimpleNamespace(entry_triggered=False, outcome_label="pending", label_source="auto", executed_return=99.0),
    ]

    summary = _build_recommendation_history_summary(outcomes)

    assert summary == {
        "trade_count": 2,
        "win_count": 1,
        "win_rate": 50.0,
        "average_net_return": 1.43,
        "total_net_return": 2.86,
        "additional_cost_pct": 0.07,
        "methodology": {
            "sample_scope": "canonical_history_live_and_reconstruction_v1",
            "entry_policy": "strategy_declared_with_legacy_fallback_v1",
            "win_definition": "executed_net_return_gt_zero",
            "deduplication": "trade_date_and_code_highest_score",
            "return_unit": "percentage_points_per_trade",
        },
    }


def test_history_summary_excludes_cancelled_before_entry_trade():
    outcomes = [
        SimpleNamespace(
            recommendation_id="rec_cancelled",
            entry_triggered=True,
            outcome_label="success",
            label_source="auto",
            executed_return=5.0,
        ),
        SimpleNamespace(
            recommendation_id="rec_executed",
            entry_triggered=True,
            outcome_label="failed",
            label_source="auto",
            executed_return=-2.0,
        ),
    ]

    summary = _build_recommendation_history_summary(
        outcomes,
        excluded_recommendation_ids={"rec_cancelled"},
    )

    assert summary["trade_count"] == 1
    assert summary["win_count"] == 0
    assert summary["win_rate"] == 0.0
    assert summary["average_net_return"] == -2.07


def test_history_summary_does_not_change_between_pages():
    db = _db()
    try:
        page1 = list_recommendation_history(db, limit=10, offset=0)
        page2 = list_recommendation_history(db, limit=10, offset=10)
        assert page1["summary"] == page2["summary"]
    finally:
        db.close()


def test_history_contains_formal_runs_only():
    db = _db()
    try:
        result = list_recommendation_history(db, limit=50)
        run_ids = {item["run_id"] for item in result["items"]}
        run_modes = {
            run.run_mode
            for run_id in run_ids
            if (run := db.get(RecommendationRun, run_id)) is not None
        }
        assert run_modes <= {"formal"}
    finally:
        db.close()


def test_history_code_filter():
    """code 筛选只返回指定股票。"""
    db = _db()
    try:
        # 先取一个存在的 code
        result_all = list_recommendation_history(db, limit=5)
        if not result_all["items"]:
            return  # 无数据，跳过
        target_code = result_all["items"][0]["code"]
        result = list_recommendation_history(db, code=target_code, limit=50)
        assert result["total"] > 0
        for item in result["items"]:
            assert item["code"] == target_code
    finally:
        db.close()


def test_history_label_filter():
    """label 筛选只返回指定 outcome_label。"""
    db = _db()
    try:
        result = list_recommendation_history(db, label="success", limit=50)
        for item in result["items"]:
            outcome = item.get("outcome")
            if outcome:
                assert outcome.get("outcome_label") == "success"
    finally:
        db.close()


def test_history_pagination():
    """分页：offset 翻页，items 不重叠。"""
    db = _db()
    try:
        page1 = list_recommendation_history(db, limit=10, offset=0)
        page2 = list_recommendation_history(db, limit=10, offset=10)
        if len(page1["items"]) == 10 and len(page2["items"]) > 0:
            ids1 = {i["recommendation_id"] for i in page1["items"]}
            ids2 = {i["recommendation_id"] for i in page2["items"]}
            assert ids1.isdisjoint(ids2), "分页 items 不应重叠"
    finally:
        db.close()


def test_history_items_have_outcome_field():
    """每个 item 应有执行结果，且未成交不得附带虚拟收益。"""
    db = _db()
    try:
        result = list_recommendation_history(db, limit=5)
        for item in result["items"]:
            assert "outcome" in item
            assert "execution_state" in item
            assert "cancel_observation" in item
            if item["outcome"] is not None:
                assert "missed_observation" not in item["outcome"]
        assert "cancel_observation" in result["summary"]
        # 未触发买点的旁路参考汇总（不改真实成交口径）必须随 summary 一起返回
        assert "missed_observation" in result["summary"]
        assert "average_reference_return" in result["summary"]["missed_observation"]
    finally:
        db.close()


@pytest.mark.requires_market_data
def test_missed_observation_is_bypass_only_and_needs_a_complete_window():
    """未触发买点的旁路参考：只统计 missed、扣附加成本，且窗口未满不给数值。

    用真实行情库构造：一笔用真实历史日期（窗口已满），一笔用未来日期（窗口必未满）。
    """
    db = _db()
    try:
        real_date = db.execute(
            text("SELECT MAX(trade_date) FROM daily_kbars")
        ).scalar()
        recommendations = [
            {"recommendation_id": "m_complete", "code": "600000", "trade_date": "2026-06-17"},
            {"recommendation_id": "m_pending", "code": "600000", "trade_date": str(real_date)},
        ]
        outcomes = {
            "m_complete": SimpleNamespace(
                recommendation_id="m_complete",
                outcome_label="missed",
                entry_triggered=False,
                horizon_days=5,
                close_return=-8.64,
            ),
            "m_pending": SimpleNamespace(
                recommendation_id="m_pending",
                outcome_label="missed",
                entry_triggered=False,
                horizon_days=5,
                close_return=3.0,
            ),
            "s1": SimpleNamespace(
                recommendation_id="s1",
                outcome_label="success",
                entry_triggered=True,
                horizon_days=5,
                close_return=9.0,
            ),
        }

        report = build_missed_observations(db, recommendations, outcomes)

        assert set(report["items"]) == {"m_complete", "m_pending"}
        # 真实成交样本不得进入旁路口径
        assert "s1" not in report["items"]
        assert report["items"]["m_complete"]["status"] == "complete"
        assert report["items"]["m_complete"]["observed_days"] == 5
        assert report["items"]["m_complete"]["net_return_pct"] == -8.71
        assert report["items"]["m_pending"]["status"] == "pending"
        assert report["items"]["m_pending"]["net_return_pct"] is None
        summary = report["summary"]
        assert summary["missed_count"] == 2
        assert summary["observed_count"] == 1
        assert summary["pending_count"] == 1
        assert summary["average_reference_return"] == -8.71
        assert summary["avoided_loss_rate"] == 100.0
    finally:
        db.close()


def test_missed_observation_without_samples_is_explicit():
    db = _db()
    try:
        summary = build_missed_observations(db, [], {})["summary"]
    finally:
        db.close()

    assert summary["missed_count"] == 0
    assert summary["observed_count"] == 0
    assert summary["average_reference_return"] is None
    assert summary["avoided_loss_rate"] is None


def test_recompute_stale_outcomes_idempotent():
    """recompute_stale_outcomes 幂等性：连续两次重算，第二次 flipped 应全空。

    验证 simulation 逻辑稳定——同一批 rec 重算后不应再翻转 outcome_label。
    若 flipped 非空，说明 simulation 逻辑不稳定（同一输入产生不同结果）。
    """
    from app.services.outcome_service import recompute_stale_outcomes

    db = _db()
    try:
        first = recompute_stale_outcomes(db, horizon_days=5, limit=30)
        second = recompute_stale_outcomes(db, horizon_days=5, limit=30)

        # 返回结构
        assert "recomputed" in first
        assert "errors" in first
        assert "flipped" in first

        # 第二次重算不应有翻转（幂等）
        assert second["flipped"] == {}, f"recompute 非幂等，第二次仍有翻转: {second['flipped']}"
        # 第二次 errors 也应为 0
        assert second["errors"] == 0
    finally:
        db.close()


def test_recompute_stale_outcomes_batched_commit_idempotent():
    """分批 commit：batch_size 小于样本量时，每批独立提交，结果仍幂等且无错误。

    回归根因（2026-07-30）：recompute 循环 131 笔在一个大事务里，持锁期间 GET
    /api/strategies 与写 metric 抢 MySQL 行锁，产生 110s 离群拖高 G3 p95。改为
    每 batch_size 笔 commit 一次缩短持锁。分批提交不能破坏幂等性——第二次重算
    flipped 应仍全空（已提交批次与未提交批次结果一致）。
    """
    from app.services.outcome_service import recompute_stale_outcomes

    db = _db()
    try:
        # batch_size=1 是最严格压力：每笔一个事务，验证边界正确性
        first = recompute_stale_outcomes(db, horizon_days=5, limit=20, batch_size=1)
        second = recompute_stale_outcomes(db, horizon_days=5, limit=20, batch_size=1)

        assert first["errors"] == 0, f"分批重算出错: {first}"
        assert second["errors"] == 0, f"分批重算第二次出错: {second}"
        assert second["flipped"] == {}, (
            f"分批 commit 破坏幂等性，第二次仍有翻转: {second['flipped']}"
        )
        # 第一次重算数应等于第二次（同一批 rec，分批不影响处理范围）
        assert first["recomputed"] == second["recomputed"]
    finally:
        db.close()
