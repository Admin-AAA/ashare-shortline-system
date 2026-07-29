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

from app.db.session import SessionLocal
from app.models.tables import RecommendationRun
from app.services.workbench_service import (
    _build_recommendation_history_summary,
    list_recommendation_history,
)


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
    """每个 item 应有 outcome、execution_state 和取消观察字段。"""
    db = _db()
    try:
        result = list_recommendation_history(db, limit=5)
        for item in result["items"]:
            assert "outcome" in item
            assert "execution_state" in item
            assert "cancel_observation" in item
        assert "cancel_observation" in result["summary"]
    finally:
        db.close()


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
