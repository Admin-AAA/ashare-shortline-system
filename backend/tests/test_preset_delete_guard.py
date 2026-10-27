import pytest
from fastapi import HTTPException

from app.api import presets as presets_api
from app.services.registry.presets import ProtectedPresetError, delete_preset


def test_formal_preset_requires_confirmation():
    with pytest.raises(ProtectedPresetError):
        delete_preset(None, "preset_formal_opening")
    with pytest.raises(ProtectedPresetError):
        delete_preset(None, "preset_formal_trend_core")


def test_api_returns_409_without_confirm():
    with pytest.raises(HTTPException) as exc:
        presets_api.delete("preset_formal_opening", confirm_formal=False, db=None)
    assert exc.value.status_code == 409
    assert "正式预设" in exc.value.detail


def test_api_deletes_with_confirm(monkeypatch):
    calls = {}

    def fake(db, preset_id, *, confirm_formal=False):
        calls["args"] = (preset_id, confirm_formal)
        return True

    monkeypatch.setattr(presets_api, "delete_preset", fake)
    assert presets_api.delete("preset_formal_opening", confirm_formal=True, db=None) == {"deleted": "preset_formal_opening"}
    assert calls["args"] == ("preset_formal_opening", True)
