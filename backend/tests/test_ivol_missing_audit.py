import logging

import app.engine.aggregator_pure as ap


def test_missing_ivol_is_recorded_not_silent(caplog):
    missing: list[str] = []
    card = {"code": "600000"}
    ap._note_missing_risk_pct(card, "strong_high_ivol_veto", "ivol20_pct", missing)
    step = card["policy_verdict_steps"][-1]
    assert step["action"] == "observe" and step["eliminated"] is False and step["risk_data_missing"] is True
    with caplog.at_level(logging.WARNING):
        ap._warn_missing_risk_pct("strong_high_ivol_veto", "ivol20_pct", missing, 3)
    assert "1/3" in caplog.text and "600000" in caplog.text


def test_coverage_alert(monkeypatch):
    import app.services.ivol_fill as f

    sent = []
    monkeypatch.setattr(f, "ivol_coverage", lambda db, d, m="ivol20": 0.1)
    monkeypatch.setattr("app.core.alerts.emit_alert", lambda *a, **k: sent.append(a) or True)
    out = f.check_ivol_coverage(object(), "2026-10-16")
    assert out["ok"] is False and sent and sent[0][0] == "ivol_coverage"


def test_single_load_ivol20_pct_map():
    import inspect

    import app.services.risk_features as rf

    assert inspect.getsource(rf).count("def load_ivol20_pct_map") == 1
