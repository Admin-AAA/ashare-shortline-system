"""略增弱反应 / 略增放量出票不得落穿通用选股池。

§519 上线 mid-forecast-slight-vol 时，把过滤和落盘插进了 forecast_slight_underreact
分支的 continue 之后。弱反应有候选（fb 不是 None）时不再 continue，掉进
_universe(db, as_of, meta["top_n"])。这两条策略的 meta 没有 top_n，于是 KeyError。
实盘 17:35 与 rebuild_replay_picks 都会走到同一段。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services import midterm_forecast as mf
from app.services import midterm_service as ms

UNDER = "mid-forecast-slight-underreact"
VOL = "mid-forecast-slight-vol"
AS_OF = "2025-10-20"


def _picks(n: int, start: int = 1) -> list[dict]:
    return [{"code": f"{i:06d}", "name": f"s{i}", "score": 200 - i} for i in range(start, start + n)]


class _DB:
    """落到通用路径时，daily_basic 查询能通过，从而暴露 meta['top_n'] 的 KeyError。"""

    def execute(self, _stmt, _params=None):
        return self

    def scalar(self):
        return AS_OF


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    # 略增两只已退出日频。出票回归仍把定义临时并回现役，防止分支再次落穿通用选股池。
    restored = dict(ms.STRATEGIES)
    restored[UNDER] = ms.RETIRED_STRATEGIES[UNDER]
    restored[VOL] = ms.RETIRED_STRATEGIES[VOL]
    monkeypatch.setattr(ms, "STRATEGIES", restored)
    monkeypatch.setattr(ms, "OUT_DIR", tmp_path)
    monkeypatch.setattr(ms, "_held_codes", lambda db, sid, d: set())
    monkeypatch.setattr(ms, "check_daily_basic_staleness", lambda *a, **k: {"stale": False})
    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(max_daily_picks_per_strategy=5),
    )
    calls = {"universe": 0}

    def boom(*_a, **_k):
        calls["universe"] += 1
        raise AssertionError("fell through to generic universe")

    monkeypatch.setattr(ms, "_universe", boom)
    monkeypatch.setattr(ms, "_lowprice_universe", boom)
    monkeypatch.setattr(ms, "_smallcap_div_universe", boom)
    return calls


def _run(strategy_ids: list[str]) -> dict:
    return ms.generate_picks(
        _DB(), AS_OF, force=True, strategy_ids=strategy_ids, apply_daily_cap=True,
    )


def test_strategies_have_no_top_n():
    assert UNDER not in ms.STRATEGIES and VOL not in ms.STRATEGIES
    assert "top_n" not in ms.RETIRED_STRATEGIES[UNDER]
    assert "top_n" not in ms.RETIRED_STRATEGIES[VOL]
    assert ms.RETIRED_STRATEGIES[UNDER]["kind"] == "forecast_slight_underreact"
    assert ms.RETIRED_STRATEGIES[VOL]["kind"] == "forecast_slight_vol"


def test_underreact_with_candidates_saves_without_top_n(monkeypatch, isolated, tmp_path):
    cands = _picks(7)
    monkeypatch.setattr(mf, "forecast_slight_underreact_candidates", lambda db, as_of, store=True: list(cands))
    monkeypatch.setattr(mf, "forecast_slight_vol_candidates", lambda *a, **k: pytest.fail("不应取放量候选"))

    res = _run([UNDER])

    assert res == {UNDER: 5}
    assert isolated["universe"] == 0
    doc = json.loads((tmp_path / f"{AS_OF}_{UNDER}.json").read_text())
    assert [p["code"] for p in doc["picks"]] == [f"{i:06d}" for i in range(1, 6)]
    assert doc["daily_cap"]["candidates_before_cap"] == 7
    assert doc["daily_cap"]["truncated_codes"] == ["000006", "000007"]


def test_vol_with_candidates_saves_without_top_n(monkeypatch, isolated, tmp_path):
    cands = _picks(3, start=10)
    monkeypatch.setattr(mf, "forecast_slight_vol_candidates", lambda db, as_of, store=True: list(cands))
    monkeypatch.setattr(mf, "forecast_slight_underreact_candidates", lambda *a, **k: pytest.fail("不应取弱反应候选"))

    res = _run([VOL])

    assert res == {VOL: 3}
    assert isolated["universe"] == 0
    doc = json.loads((tmp_path / f"{AS_OF}_{VOL}.json").read_text())
    assert [p["code"] for p in doc["picks"]] == ["000010", "000011", "000012"]
    assert "daily_cap" not in doc


def test_both_strategies_save_their_own_candidates(monkeypatch, isolated, tmp_path):
    under = _picks(6, start=1)
    vol = _picks(6, start=20)
    monkeypatch.setattr(mf, "forecast_slight_underreact_candidates", lambda db, as_of, store=True: list(under))
    monkeypatch.setattr(mf, "forecast_slight_vol_candidates", lambda db, as_of, store=True: list(vol))

    res = _run([UNDER, VOL])

    assert res[UNDER] == 5
    assert res[VOL] == 5
    assert isolated["universe"] == 0
    under_doc = json.loads((tmp_path / f"{AS_OF}_{UNDER}.json").read_text())
    vol_doc = json.loads((tmp_path / f"{AS_OF}_{VOL}.json").read_text())
    assert [p["code"] for p in under_doc["picks"]] == [f"{i:06d}" for i in range(1, 6)]
    assert [p["code"] for p in vol_doc["picks"]] == [f"{i:06d}" for i in range(20, 25)]


@pytest.mark.parametrize("sid,attr", [
    (UNDER, "forecast_slight_underreact_candidates"),
    (VOL, "forecast_slight_vol_candidates"),
])
def test_empty_or_missing_candidates_do_not_fall_through(monkeypatch, isolated, sid, attr):
    monkeypatch.setattr(mf, attr, lambda db, as_of, store=True: [])
    assert _run([sid]) == {sid: 0}
    monkeypatch.setattr(mf, attr, lambda db, as_of, store=True: None)
    assert _run([sid]) == {sid: "no_data"}
    assert isolated["universe"] == 0


def test_underreact_excludes_held_before_daily_cap(monkeypatch, isolated, tmp_path):
    monkeypatch.setattr(ms, "_held_codes", lambda db, sid, d: {"000001"})
    monkeypatch.setattr(mf, "forecast_slight_underreact_candidates", lambda db, as_of, store=True: _picks(7))

    res = _run([UNDER])

    assert res == {UNDER: 5}
    assert isolated["universe"] == 0
    doc = json.loads((tmp_path / f"{AS_OF}_{UNDER}.json").read_text())
    assert [p["code"] for p in doc["picks"]] == [f"{i:06d}" for i in range(2, 7)]
    assert doc["daily_cap"]["candidates_before_cap"] == 6
