import app.services.midterm_service as midterm_service
from app.services import scheduler_service as ss


class _DB:
    def __init__(self):
        self.rolled = 0

    def rollback(self):
        self.rolled += 1


def test_hook_targets_evening_repair_and_premarket_tasks():
    assert ss.BLOCK_TRADE_HOOK_TASK_IDS == {"postclose_late_data_repair", "strategy_exposure_run"}


def test_hook_calls_generate_with_latest_bar_date(monkeypatch):
    calls = []
    monkeypatch.setattr(ss, "_latest_allowed_bar_date", lambda: "2026-10-16")
    monkeypatch.setattr(midterm_service, "generate_block_trade_picks", lambda db, t: calls.append(t) or {"picks": 3})
    out = ss._run_block_trade_picks_hook(_DB(), "postclose_late_data_repair")
    assert calls == ["2026-10-16"] and out["result"] == {"picks": 3} and out["trade_date"] == "2026-10-16"


def test_hook_failure_is_swallowed_and_logged(monkeypatch, caplog):
    monkeypatch.setattr(ss, "_latest_allowed_bar_date", lambda: "2026-10-16")

    def boom(db, t):
        raise RuntimeError("tushare down")

    monkeypatch.setattr(midterm_service, "generate_block_trade_picks", boom)
    db = _DB()
    out = ss._run_block_trade_picks_hook(db, "strategy_exposure_run")
    assert "RuntimeError" in out["error"] and db.rolled == 1
    assert any("block-trade picks failed" in r.message for r in caplog.records)
