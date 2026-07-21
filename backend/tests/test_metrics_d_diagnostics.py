"""D 项诊断端点 /api/metrics/d-diagnostics 回归（T32/T34/T35）。"""
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
    assert "phase_information" in data
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


def test_d_diagnostics_phase_information_when_present():
    """有足够 LIVE formal 时 phase_information 含 χ² 字段。"""
    client = TestClient(app)
    data = client.get("/api/metrics/d-diagnostics").json()
    pi = data.get("phase_information")
    if pi is None:
        return
    assert pi["n"] >= 30
    assert "regime_p" in pi and "sentiment_p" in pi
    assert "regime_significant" in pi and "sentiment_significant" in pi
    assert isinstance(pi.get("notable_low_buckets"), list)
    assert pi["regime_significant"] is True
    assert pi["sentiment_significant"] is False


def test_d_diagnostics_modes_sorted_desc():
    client = TestClient(app)
    data = client.get("/api/metrics/d-diagnostics").json()
    modes = data.get("modes") or []
    if len(modes) >= 2:
        assert modes[0]["n"] >= modes[1]["n"]


def test_d_diagnostics_triage_matches_live_residual():
    """T35：triage 分层和 = residual_n。"""
    client = TestClient(app)
    data = client.get("/api/metrics/d-diagnostics").json()
    if data["residual_n"] <= 0:
        return
    tr = data.get("triage")
    assert tr is not None, "residual>0 时必须返回 triage"
    assert tr["n"] == data["residual_n"]
    s = tr["pure_pollution"] + tr["pseudo_active"] + tr["day_clustered"] + tr["sparse"]
    assert s == tr["n"]
