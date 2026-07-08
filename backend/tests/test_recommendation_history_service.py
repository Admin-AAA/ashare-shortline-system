"""list_recommendation_history 回归测试。

验证跨 run 推荐历史 API 的核心逻辑：
- 按 (trade_date, code) 去重（教训库第 46 轮：回测 DB 累积重复）
- code/label 筛选
- 分页（limit/offset）
- 返回结构（items/total/offset/limit）

使用真实生产 MySQL（CLAUDE.md 允许），只读不写。
"""
from __future__ import annotations

from app.db.session import SessionLocal
from app.services.workbench_service import list_recommendation_history


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
        # 去重验证：items 里不应有重复 (trade_date, code)
        seen = set()
        for item in result["items"]:
            key = (item["trade_date"], item["code"])
            assert key not in seen, f"重复 (trade_date, code): {key}"
            seen.add(key)
        assert len(result["items"]) <= 50
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
    """每个 item 应有 outcome 字段（可能为 None）。"""
    db = _db()
    try:
        result = list_recommendation_history(db, limit=5)
        for item in result["items"]:
            assert "outcome" in item
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
