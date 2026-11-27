"""短线回放版本检查：回放批次指纹与最新实盘指纹不同即黄；缺批次黄；一致绿。"""
from app.services import data_quality_monitor as dq


def _row(sid, live, replay, **kw):
    return {"strategy_id": sid, "live_fingerprint": live, "live_date": "2026-11-26",
            "replay_fingerprint": replay, "replay_completed_at": "2026-10-19", "replay_end": "2026-10-16",
            "replay_days": 241, **kw}


def test_stale_and_missing_turn_yellow():
    item = dq.short_replay_version_item([
        _row("a", "fp-live", "fp-old"), _row("b", "fp-same", "fp-same"), _row("c", "fp-live", None, replay_end=None),
    ])
    assert item["severity"] == dq.YELLOW and item["key"] == dq.SHORT_REPLAY_VERSION_KEY
    assert "a（回放 2026-10-19 建、覆盖到 2026-10-16" in item["detail"] and "没有已验证回放批次：c" in item["detail"]
    assert "replay_all_days.py" in item["detail"]


def test_all_same_version_green():
    item = dq.short_replay_version_item([_row("a", "x", "x"), _row("b", "y", "y")])
    assert item["severity"] == dq.GREEN and "同版本" in item["detail"]


def test_no_live_run_yet_is_not_stale():
    # 策略刚上线还没实盘 run：有回放批次就不报落后
    item = dq.short_replay_version_item([_row("a", None, "x", live_date=None)])
    assert item["severity"] == dq.GREEN
