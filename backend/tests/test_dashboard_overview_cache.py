"""S8 dashboard overview must not own a second response cache."""
from unittest.mock import MagicMock

from app.api import dashboard as dashboard_mod


def test_overview_reads_authoritative_projection_on_each_request(monkeypatch):
    calls: list[object] = []

    def fake_build(_db):
        value = object()
        calls.append(value)
        return value

    monkeypatch.setattr(dashboard_mod, "_build_overview_payload", fake_build)
    first = dashboard_mod.overview(db=MagicMock())
    second = dashboard_mod.overview(db=MagicMock())

    assert first is calls[0]
    assert second is calls[1]
    assert first is not second
    assert len(calls) == 2


def test_dashboard_module_has_no_response_cache_or_refresh_worker():
    assert not hasattr(dashboard_mod, "_OVERVIEW_CACHE")
    assert not hasattr(dashboard_mod, "_schedule_overview_refresh")
    assert not hasattr(dashboard_mod, "_overview_refresh_worker")
