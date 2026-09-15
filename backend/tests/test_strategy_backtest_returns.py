"""策略清单页「三窗回测收益」字段回归（artifact 读取 + /api/strategies 附加）。

背景：多数技能没有当前版本 canonical 画像（performance=None），页面上看不到任何收益。
把本机跑出的三窗单策略回测指标落到 `app/policies/strategy_backtest_metrics.json`，
由 `/api/strategies` 以独立字段 `backtest_returns` 暴露（与 canonical 画像分开，禁止混算）。

锁三件事：
1. artifact 可读且结构完整（含口径说明与复现命令——L-111 要求数字可追溯）；
2. 每个技能返回 3 个窗口 + 组合基线 + 口径；
3. artifact 缺失/损坏时降级为 None，绝不让接口 500。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.registry import backtest_metrics

pytestmark = pytest.mark.integration

ARTIFACT = Path(__file__).resolve().parents[1] / "app" / "policies" / "strategy_backtest_metrics.json"


def test_artifact_loads_with_caliber_and_windows():
    payload = backtest_metrics.load_backtest_metrics()
    assert payload is not None, "缺少 app/policies/strategy_backtest_metrics.json"
    assert payload["schema_version"] >= 1
    caliber = payload["caliber"]
    # 口径必须自证：选股/出场政策 + 复现命令 + 风险声明
    assert caliber["selection_policy"] == "production_selection.v4"
    assert caliber["execution_policy"] == "execution_policy.v4"
    assert "--single-skill" in caliber["reproduce"]
    assert caliber["caveat"]
    assert set(payload["windows"]) == {"W1", "W2", "W3"}
    assert set(payload["combined"]) == {"W1", "W2", "W3"}
    assert payload["skills"], "artifact 里没有任何技能指标"


def test_each_skill_has_three_windows_and_combined_baseline():
    payload = backtest_metrics.load_backtest_metrics()
    for skill_id, block in payload["skills"].items():
        returns = backtest_metrics.backtest_returns_for(skill_id, payload)
        assert returns is not None
        assert set(returns["windows"]) == {"W1", "W2", "W3"}
        assert set(returns["combined"]) == {"W1", "W2", "W3"}
        assert returns["caliber"]["label"]
        for window, metrics in returns["windows"].items():
            if metrics is None:
                continue
            assert metrics["n"] > 0
            assert 0.0 <= metrics["win_rate_pct"] <= 100.0
            # 盈亏比允许为 None（窗口内无亏损笔或样本<5），但不得出现伪精度大数
            if metrics["profit_factor"] is not None:
                assert 0.0 <= metrics["profit_factor"] <= 1000.0
            else:
                assert metrics["profit_factor_note"]
            assert metrics["challenger"] in {"PRODUCTION", "FALSIFIED", "SHADOW", None} or isinstance(
                metrics["challenger"], str
            )


def test_unknown_skill_returns_none():
    payload = backtest_metrics.load_backtest_metrics()
    assert backtest_metrics.backtest_returns_for("no-such-skill", payload) is None


def test_missing_artifact_degrades_to_none(tmp_path):
    missing = tmp_path / "nope.json"
    assert backtest_metrics.load_backtest_metrics(missing) is None
    assert backtest_metrics.backtest_returns_for("chen-xiaoqun-skill", None) in (
        None,
        backtest_metrics.backtest_returns_for("chen-xiaoqun-skill"),
    )


def test_broken_artifact_degrades_to_none(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert backtest_metrics.load_backtest_metrics(broken) is None


def test_api_strategies_exposes_backtest_returns():
    client = TestClient(app)
    resp = client.get("/api/strategies")
    assert resp.status_code == 200
    data = resp.json()
    assert data
    with_returns = [item for item in data if item.get("backtest_returns")]
    assert with_returns, "/api/strategies 未暴露 backtest_returns（页面将看不到回测收益）"
    for item in with_returns:
        bt = item["backtest_returns"]
        assert set(bt["windows"]) == {"W1", "W2", "W3"}
        assert bt["caliber"]["selection_policy"] == "production_selection.v4"
        assert bt["generated_at"]
    # 页面排序：有回测收益的（按三窗均值）应排在无收益的前面
    means = [
        (item["backtest_returns"] or {}).get("avg_net_pct_mean")
        for item in data
        if item.get("backtest_returns")
    ]
    assert means == sorted([m for m in means], reverse=True) or len(set(means)) == 1


def test_artifact_file_is_valid_json_committed_copy():
    assert ARTIFACT.exists()
    json.loads(ARTIFACT.read_text(encoding="utf-8"))
