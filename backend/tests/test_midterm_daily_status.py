import app.services.midterm_service as m


def test_classify():
    c = m.classify_daily_status
    assert c(3) == "ok" and c(0) == "no_signal" and c("no_data") == "data_error"
    assert c({"skipped": "slots_full", "held": 20}) == "slots_full"
    assert c({"skipped": "all_held"}) == "no_signal"
    assert c({"skipped": "small_cap_below_ma20"}) == "no_signal"
    assert c({"skipped": "not_signal_day"}) == "not_signal_day"
    assert c({"error": "X"}) == "data_error"


def test_record_and_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "OUT_DIR", tmp_path / "picks")
    m._record_status("2026-10-15", {"a": 2})
    m._record_status("2026-10-16", {"a": {"skipped": "slots_full", "held": 20}, "__current__": "x"})
    m._record_status("2026-10-16", {"b": "no_data"})
    st = m.latest_daily_status("a")
    assert st["date"] == "2026-10-16" and st["status"] == "slots_full" and st["detail"] == "slots_full"
    assert m.latest_daily_status("b")["status"] == "data_error"
    assert m.latest_daily_status("zzz") is None


def test_overview_has_daily_status(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "OUT_DIR", tmp_path / "picks")
    rows = m.strategies_overview("mid", db=object()) if False else None
    m._record_status("2026-10-16", {"mid-repurchase-plan": 1})
    assert m.latest_daily_status("mid-repurchase-plan")["status"] == "ok"
