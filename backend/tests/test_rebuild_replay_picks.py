import importlib.util
import json
import sys
from pathlib import Path

import pytest

_P = Path(__file__).resolve().parents[1] / "scripts" / "rebuild_replay_picks.py"
spec = importlib.util.spec_from_file_location("rebuild_replay_picks", _P)
rr = importlib.util.module_from_spec(spec)
sys.modules["rebuild_replay_picks"] = rr
spec.loader.exec_module(rr)


def test_replay_midlong_isolated_and_tagged(tmp_path, monkeypatch):
    from app.services import midterm_service as ms
    prod = tmp_path / "prod" / "picks"
    prod.mkdir(parents=True)
    (prod / "2026-10-16_mid-repurchase-plan.json").write_text(json.dumps(
        {"strategy_id": "mid-repurchase-plan", "signal_date": "2026-10-16", "picks": [{"code": "000001"}]}))
    monkeypatch.setattr(ms, "OUT_DIR", prod)
    monkeypatch.setattr(rr, "_trade_dates", lambda db, s, e: ["2026-10-15", "2026-10-16"])

    def fake_generate(db, as_of, strategy_ids=None, **kw):
        assert ms.OUT_DIR != prod
        assert kw.get("apply_daily_cap") is True
        assert ms._REPLAY_OVERWRITE.get() is True
        if as_of == "2026-10-15":
            (ms.OUT_DIR / f"{as_of}_mid-repurchase-plan.json").write_text(json.dumps(
                {"strategy_id": "mid-repurchase-plan", "signal_date": as_of, "picks": [{"code": "600000"}]}))
            return {"mid-repurchase-plan": 1}
        return {"mid-repurchase-plan": "no_data"}

    monkeypatch.setattr(ms, "generate_picks", fake_generate)
    # 不等真实重试间隔（120+300+600+900s）：否则本测试把全量回归卡住 32 分钟
    monkeypatch.setattr(rr, "MID_RETRY_WAITS", ())
    staging = tmp_path / "stg" / "picks"
    stats = rr.replay_midlong(None, "2026-10-15", "2026-10-16", None, staging)
    assert ms.OUT_DIR == prod
    assert len(list(prod.glob("*.json"))) == 1  # 生产目录未被改动
    d = json.loads((staging / "2026-10-15_mid-repurchase-plan.json").read_text())
    assert d["origin"] == "replay"
    assert stats["no_data"] == {"mid-repurchase-plan": ["2026-10-16"]}
    assert stats["by_strategy"]["mid-repurchase-plan"] == {"batches": 1, "picks": 1}


def test_replay_midlong_default_cap_writes_audit_and_overwrites(tmp_path, monkeypatch):
    """中线/长线共用 replay_midlong：默认套每日上限（含同分顺延），并写入 daily_cap；过了入场截止仍覆盖。"""
    from types import SimpleNamespace

    from app.services import midterm_service as ms

    assert rr.replay_midlong.__kwdefaults__["apply_daily_cap"] is True

    prod = tmp_path / "prod"
    prod.mkdir()
    (prod / "keep.json").write_text("{}")
    monkeypatch.setattr(ms, "OUT_DIR", prod)
    monkeypatch.setattr(rr, "_trade_dates", lambda db, s, e: ["2026-10-16"])
    monkeypatch.setattr(rr, "MID_RETRY_WAITS", ())
    monkeypatch.setattr(rr, "_local_forecast_reader", lambda api: api)
    monkeypatch.setattr(ms, "_held_codes", lambda db, sid, d: set())
    monkeypatch.setattr("app.services.midterm_live_seal.past_deadline", lambda *_a, **_k: True)
    monkeypatch.setattr("app.core.config.get_settings", lambda: SimpleNamespace(max_daily_picks_per_strategy=5))
    scores = [100, 90, 80, 70, 60, 60, 50]
    state = {"picks": [{"code": f"{i:06d}", "name": "n", "score": s} for i, s in enumerate(scores, 1)]}
    monkeypatch.setattr(ms, "earnings_accel_candidates", lambda db, d: state["picks"])

    staging = tmp_path / "stg" / "picks"
    stats = rr.replay_midlong(None, "2026-10-16", "2026-10-16", ["mid-earnings-accel"], staging)
    doc = json.loads((staging / "2026-10-16_mid-earnings-accel.json").read_text())
    assert doc["origin"] == "replay"
    assert [p["code"] for p in doc["picks"]] == [f"{i:06d}" for i in range(1, 7)]
    assert doc["daily_cap"]["cap"] == 5
    assert doc["daily_cap"]["cap_unit"] == "top_n_with_ties"
    assert doc["daily_cap"]["truncated_count"] == 1
    assert doc["daily_cap"]["truncated_codes"] == ["000007"]
    assert stats["by_strategy"]["mid-earnings-accel"] == {"batches": 1, "picks": 6}
    assert list(prod.glob("*.json")) == [prod / "keep.json"]

    (staging.parent / "progress.json").unlink()
    state["picks"] = [{"code": f"{i:06d}", "name": "n", "score": 10 - i} for i in range(1, 4)]
    rr.replay_midlong(None, "2026-10-16", "2026-10-16", ["mid-earnings-accel"], staging)
    doc = json.loads((staging / "2026-10-16_mid-earnings-accel.json").read_text())
    assert [p["code"] for p in doc["picks"]] == ["000001", "000002", "000003"]
    assert "daily_cap" not in doc
    assert ms.OUT_DIR == prod


def test_onboard_disabled_by_default(monkeypatch):
    monkeypatch.delenv(rr.ONBOARD_ENV, raising=False)
    assert rr.onboard_strategy(None, "mid-repurchase-plan")["skipped"]


def test_blackout_window():
    import datetime as dt
    assert rr._in_blackout(dt.datetime(2026, 10, 19, 17, 35))
    assert not rr._in_blackout(dt.datetime(2026, 10, 19, 19, 0))


def test_delete_order_children_first():
    names = [t for t, _ in rr.SHORT_DELETE_ORDER]
    assert names.index("recommendation_outcome_versions") < names.index("recommendations") < names.index("recommendation_runs")
