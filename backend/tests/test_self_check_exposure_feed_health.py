"""E 项曝光补跑健康三分法：最新 0 产出覆盖旧推荐（T25）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "self_check.py"
_spec = importlib.util.spec_from_file_location("self_check_feed", _SCRIPTS)
sc = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(sc)


def test_latest_zero_exp_overrides_old_rec():
    """07-16 有推荐 + 07-21 最新补跑 0 产出 → gate_blocked，非 feeding。"""
    under = ["chip-single-peak-breakout", "small-cap-thematic-momentum"]
    last_rec = {
        "chip-single-peak-breakout": ("2026-07-16 00:03:46", 1),
        "small-cap-thematic-momentum": ("2026-07-16 00:04:08", 1),
    }
    last_exp = {
        "chip-single-peak-breakout": ("2026-07-21 00:03:00", 0),
        "small-cap-thematic-momentum": ("2026-07-21 00:03:09", 0),
    }
    h = sc._classify_exposure_feed_health(under, last_rec=last_rec, last_exp=last_exp)
    assert h["feeding"] == []
    assert set(h["gate_blocked"]) == set(under)
    assert h["stalled"] == []


def test_latest_exp_with_output_is_feeding():
    under = ["chip-single-peak-breakout"]
    last_rec = {"chip-single-peak-breakout": ("2026-07-21 00:03:46", 2)}
    last_exp = {"chip-single-peak-breakout": ("2026-07-21 00:03:00", 2)}
    h = sc._classify_exposure_feed_health(under, last_rec=last_rec, last_exp=last_exp)
    assert len(h["feeding"]) == 1
    assert "chip-single-peak-breakout" in h["feeding"][0]
    assert h["gate_blocked"] == []
    assert h["stalled"] == []


def test_no_exp_is_stalled():
    under = ["trend-pullback-continuation"]
    last_rec = {"trend-pullback-continuation": ("2026-07-16 00:04:17", 1)}
    last_exp: dict = {}
    h = sc._classify_exposure_feed_health(under, last_rec=last_rec, last_exp=last_exp)
    assert h["stalled"] == ["trend-pullback-continuation"]
    assert h["feeding"] == []
    assert h["gate_blocked"] == []


def test_exp_positive_without_rec_still_feeding():
    """exp 有产出但 last_rec 空（窗口边界）→ 仍算在喂。"""
    under = ["x"]
    h = sc._classify_exposure_feed_health(
        under,
        last_rec={},
        last_exp={"x": ("2026-07-21 01:00:00", 3)},
    )
    assert len(h["feeding"]) == 1
    assert "exp@" in h["feeding"][0]
