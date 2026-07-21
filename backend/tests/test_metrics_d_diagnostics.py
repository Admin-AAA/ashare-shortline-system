"""D 项诊断端点 /api/metrics/d-diagnostics 回归（T32）。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_d_diagnostics_shape():
    client = TestClient(app)
    resp = client.get("/api/metrics/d-diagnostics")
    assert resp.status_code == 200
    data = resp.json()
    assert "residual_n" in data
    assert isinstance(data["residual_n"], int)
    assert "modes" in data
    assert isinstance(data["modes"], list)
    assert "pseudo_active" in data
    # 生产库当前 residual >0 且有弱活伪活跃
    if data["residual_n"] > 0 and data["pseudo_active"]:
        pa = data["pseudo_active"]
        assert pa["weak_active_n"] >= pa["pseudo_n"]
        assert "days" in pa
        assert isinstance(pa["days"], list)


def test_d_diagnostics_modes_sorted_desc():
    client = TestClient(app)
    data = client.get("/api/metrics/d-diagnostics").json()
    modes = data.get("modes") or []
    if len(modes) >= 2:
        assert modes[0]["n"] >= modes[1]["n"]
