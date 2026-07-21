"""D 项诊断端点 /api/metrics/d-diagnostics 回归（T32/T34）。"""
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
    # phase 信息量（T34）：与 self_check 同源
    assert "phase_information" in data
    # 生产库当前 residual >0 且有弱活伪活跃
    if data["residual_n"] > 0 and data["pseudo_active"]:
        pa = data["pseudo_active"]
        assert pa["weak_active_n"] >= pa["pseudo_n"]
        assert "days" in pa
        assert isinstance(pa["days"], list)


def test_d_diagnostics_phase_information_when_present():
    """有足够 LIVE formal 时 phase_information 含 χ² 字段。"""
    client = TestClient(app)
    data = client.get("/api/metrics/d-diagnostics").json()
    pi = data.get("phase_information")
    if pi is None:
        return  # 样本不足时允许空
    assert pi["n"] >= 30
    assert "regime_p" in pi and "sentiment_p" in pi
    assert "regime_significant" in pi and "sentiment_significant" in pi
    assert isinstance(pi.get("notable_low_buckets"), list)
    # 当前生产：regime 显著、sentiment 不显著（T5 实证）
    assert pi["regime_significant"] is True
    assert pi["sentiment_significant"] is False


def test_d_diagnostics_modes_sorted_desc():
    client = TestClient(app)
    data = client.get("/api/metrics/d-diagnostics").json()
    modes = data.get("modes") or []
    if len(modes) >= 2:
        assert modes[0]["n"] >= modes[1]["n"]
