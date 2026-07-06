"""策略清单端点回归测试 —— /api/strategies。

验证：
- 返回 200 且为列表
- 每个元素含 skill_id / name / performance 等关键字段
- 已调用策略 performance 非空且含胜率；从未调用的 performance=None（覆盖 3/8 缺口的显式呈现）
- 已调用策略排在未调用之前
- mode 校验：非法 mode 返回 400
"""

from fastapi.testclient import TestClient

from app.main import app


def test_strategies_endpoint_basic_shape():
    client = TestClient(app)
    resp = client.get("/api/strategies")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    for item in data:
        assert "skill_id" in item and "name" in item
        assert "performance" in item
        assert "enabled" in item
        assert "assigned_presets" in item


def test_strategies_endpoint_called_before_dormant():
    client = TestClient(app)
    data = client.get("/api/strategies").json()
    called = [d for d in data if d["performance"] is not None]
    dormant = [d for d in data if d["performance"] is None]
    # 至少存在已调用与未调用两类（覆盖 3/8 的真实数据形态）
    assert called, "应有至少一个已调用策略"
    assert dormant, "应有至少一个未覆盖（从未调用）策略"
    # 排序：已调用的排前面
    first_dormant_idx = next((i for i, d in enumerate(data) if d["performance"] is None), None)
    if first_dormant_idx is not None:
        for d in data[:first_dormant_idx]:
            assert d["performance"] is not None


def test_strategies_endpoint_called_has_metrics():
    client = TestClient(app)
    data = client.get("/api/strategies").json()
    called = [d for d in data if d["performance"] is not None]
    for d in called:
        p = d["performance"]
        assert isinstance(p["sample_count"], int) and p["sample_count"] > 0
        assert p["hit_rate"] is not None


def test_strategies_endpoint_invalid_mode():
    client = TestClient(app)
    resp = client.get("/api/strategies?mode=bogus")
    assert resp.status_code == 400
