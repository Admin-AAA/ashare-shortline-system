"""D 项诊断端点 /api/metrics/d-diagnostics 回归（T32/T35）。"""
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
    assert "triage" in data
    # 生产库当前 residual >0 且有弱活伪活跃
    if data["residual_n"] > 0 and data["pseudo_active"]:
        pa = data["pseudo_active"]
        assert pa["weak_active_n"] >= pa["pseudo_n"]
        assert "days" in pa
        assert isinstance(pa["days"], list)
    if data["residual_n"] > 0 and data["triage"]:
        tr = data["triage"]
        assert tr["n"] == data["residual_n"]
        assert (
            tr["pure_pollution"]
            + tr["pseudo_active"]
            + tr["day_clustered"]
            + tr["sparse"]
            == tr["n"]
        )
        assert "note" in tr


def test_d_diagnostics_modes_sorted_desc():
    client = TestClient(app)
    data = client.get("/api/metrics/d-diagnostics").json()
    modes = data.get("modes") or []
    if len(modes) >= 2:
        assert modes[0]["n"] >= modes[1]["n"]


def test_d_diagnostics_triage_matches_live_residual():
    """T35：triage 分层和 = residual_n（live 13=8+2+0+3 护栏）。"""
    client = TestClient(app)
    data = client.get("/api/metrics/d-diagnostics").json()
    if data["residual_n"] <= 0:
        return
    tr = data.get("triage")
    assert tr is not None, "residual>0 时必须返回 triage"
    assert tr["n"] == data["residual_n"]
    s = tr["pure_pollution"] + tr["pseudo_active"] + tr["day_clustered"] + tr["sparse"]
    assert s == tr["n"]
