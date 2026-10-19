import pytest

from app.services.registry.presets import ProtectedPresetError, delete_preset


def test_formal_preset_cannot_be_deleted():
    with pytest.raises(ProtectedPresetError):
        delete_preset(None, "preset_formal_opening")
    with pytest.raises(ProtectedPresetError):
        delete_preset(None, "preset_formal_trend_core")
