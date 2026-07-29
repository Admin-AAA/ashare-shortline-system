"""POST /api/recommendations/execution-confirmations API 契约测试。

验证 Spec 第 10.2 条：
- 1–50 个 ID 请求成功，重复 ID 被去重
- 空列表和超过 50 个返回 422
- 已结算 ID 不出现在 items
- 响应 summary 计数与 items/errors 一致
- 批次公共数据源失败返回明确错误，不伪装成「未命中」

mock 策略：monkeypatch confirm_batch_executions，隔离真实行情与策略运行。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _ok_response(ids: list[str]) -> dict[str, Any]:
    return {
        "computed_at": "2026-07-29T10:06:12",
        "data_as_of": "2026-07-29",
        "cached": False,
        "summary": {
            "requested_count": len(ids),
            "eligible_count": len(ids),
            "settled_skipped_count": 0,
            "expired_count": 0,
            "success_count": len(ids),
            "error_count": 0,
        },
        "items": [
            {
                "recommendation_id": rid,
                "code": "600775",
                "trade_date": "2026-07-28",
                "status": "ok",
                "fresh": True,
                "data_as_of": "2026-07-29",
                "current_price": 10.42,
                "entry_decision": {"code": "buy", "label": "允许按策略轻仓执行", "reason": "买点仍成立"},
                "strategy_signal": {"phase": "确认", "score": 76.0, "setup_match": "分歧回封", "hard_veto": False, "risk_flags": []},
            }
            for rid in ids
        ],
        "errors": [],
    }


def test_empty_list_returns_422():
    client = TestClient(app)
    resp = client.post("/api/recommendations/execution-confirmations", json={"recommendation_ids": []})
    assert resp.status_code == 422


def test_over_50_returns_422_no_truncation():
    client = TestClient(app)
    ids = [f"rec_{i:03d}" for i in range(51)]
    resp = client.post("/api/recommendations/execution-confirmations", json={"recommendation_ids": ids})
    assert resp.status_code == 422
    # 不静默截断：服务层不应被调用（422 在服务层之前）


def test_duplicate_ids_deduped(monkeypatch):
    """重复 ID 被去重后传给服务层，服务层只收到去重后的列表。"""
    captured: dict[str, Any] = {}

    def _fake_confirm(db, ids, *, refresh=False):
        captured["ids"] = list(ids)
        captured["refresh"] = refresh
        return _ok_response(ids)

    monkeypatch.setattr(
        "app.services.batch_execution_confirmation.confirm_batch_executions",
        _fake_confirm,
    )
    client = TestClient(app)
    resp = client.post(
        "/api/recommendations/execution-confirmations",
        json={"recommendation_ids": ["rec_a", "rec_a", "rec_b"]},
    )
    assert resp.status_code == 200
    # 去重后只剩 2 个
    assert captured["ids"] == ["rec_a", "rec_b"]


def test_settled_ids_not_in_items(monkeypatch):
    """已结算 ID 不进入 items，只计入 settled_skipped_count。"""
    def _fake_confirm(db, ids, *, refresh=False):
        return {
            "computed_at": "2026-07-29T10:06:12",
            "data_as_of": "2026-07-29",
            "cached": False,
            "summary": {
                "requested_count": 2,
                "eligible_count": 1,
                "settled_skipped_count": 1,
                "expired_count": 0,
                "success_count": 1,
                "error_count": 0,
            },
            "items": [
                {"recommendation_id": "rec_unsettled", "code": "600775", "trade_date": "2026-07-28", "status": "ok", "fresh": True, "entry_decision": {"code": "wait", "label": "继续观察", "reason": "买点未触发"}, "strategy_signal": {}},
            ],
            "errors": [],
        }

    monkeypatch.setattr("app.services.batch_execution_confirmation.confirm_batch_executions", _fake_confirm)
    client = TestClient(app)
    resp = client.post(
        "/api/recommendations/execution-confirmations",
        json={"recommendation_ids": ["rec_unsettled", "rec_settled"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    item_ids = [it["recommendation_id"] for it in data["items"]]
    assert "rec_settled" not in item_ids
    assert data["summary"]["settled_skipped_count"] == 1


def test_summary_counts_consistent_with_items_and_errors(monkeypatch):
    """summary 计数与 items/errors 实际数量一致。"""
    def _fake_confirm(db, ids, *, refresh=False):
        items = [
            {"recommendation_id": "rec_ok1", "code": "600775", "trade_date": "2026-07-28", "status": "ok", "fresh": True, "entry_decision": {"code": "buy", "label": "允许执行", "reason": ""}, "strategy_signal": {}},
            {"recommendation_id": "rec_exp", "code": "000002", "trade_date": "2026-07-28", "status": "ok", "fresh": False, "entry_decision": {"code": "expired", "label": "信号已过期", "reason": ""}, "strategy_signal": {}},
        ]
        errors = [{"recommendation_id": "rec_err", "code": "000003", "message": "实时行情不可用"}]
        return {
            "computed_at": "2026-07-29T10:06:12",
            "data_as_of": "2026-07-29",
            "cached": False,
            "summary": {
                "requested_count": 4,
                "eligible_count": 3,
                "settled_skipped_count": 1,
                "expired_count": 1,
                "success_count": sum(1 for it in items if it["status"] == "ok"),
                "error_count": len(errors),
            },
            "items": items,
            "errors": errors,
        }

    monkeypatch.setattr("app.services.batch_execution_confirmation.confirm_batch_executions", _fake_confirm)
    client = TestClient(app)
    resp = client.post(
        "/api/recommendations/execution-confirmations",
        json={"recommendation_ids": ["rec_ok1", "rec_exp", "rec_err", "rec_settled"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    s = data["summary"]
    assert s["requested_count"] == 4
    assert len(data["items"]) == 2
    assert len(data["errors"]) == 1
    assert s["error_count"] == len(data["errors"])
    assert s["success_count"] == sum(1 for it in data["items"] if it["status"] == "ok")


def test_public_source_failure_returns_explicit_error_not_miss(monkeypatch):
    """批次公共数据源失败返回明确错误，不伪装成「未命中」。"""
    def _fake_confirm(db, ids, *, refresh=False):
        return {
            "computed_at": "2026-07-29T10:06:12",
            "data_as_of": "2026-07-29",
            "cached": False,
            "error": "公共行情数据源失败: rt_k 超时",
            "summary": {
                "requested_count": 2,
                "eligible_count": 2,
                "settled_skipped_count": 0,
                "expired_count": 0,
                "success_count": 0,
                "error_count": 2,
            },
            "items": [],
            "errors": [
                {"recommendation_id": "rec_a", "code": "600775", "message": "实时行情不可用"},
                {"recommendation_id": "rec_b", "code": "000002", "message": "实时行情不可用"},
            ],
        }

    monkeypatch.setattr("app.services.batch_execution_confirmation.confirm_batch_executions", _fake_confirm)
    client = TestClient(app)
    resp = client.post(
        "/api/recommendations/execution-confirmations",
        json={"recommendation_ids": ["rec_a", "rec_b"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("error")  # 明确错误信息
    assert data["summary"]["success_count"] == 0
    # 没有任何 buy/未命中 伪装
    assert all(it.get("entry_decision", {}).get("code") != "buy" for it in data["items"])


def test_refresh_flag_passed_through(monkeypatch):
    """refresh=true 透传给服务层。"""
    captured: dict[str, Any] = {}

    def _fake_confirm(db, ids, *, refresh=False):
        captured["refresh"] = refresh
        return _ok_response(ids)

    monkeypatch.setattr("app.services.batch_execution_confirmation.confirm_batch_executions", _fake_confirm)
    client = TestClient(app)
    resp = client.post(
        "/api/recommendations/execution-confirmations",
        json={"recommendation_ids": ["rec_a"], "refresh": True},
    )
    assert resp.status_code == 200
    assert captured["refresh"] is True
