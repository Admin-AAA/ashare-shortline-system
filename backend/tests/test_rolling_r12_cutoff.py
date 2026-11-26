"""R12 中长线口径回归：只算卖出日 ≤ cutoff 的笔；截止日后才卖出的算持有中，单列「最近已卖出」，不进正负判定。"""
from __future__ import annotations

import json

from app.services import midterm_service as ms
from app.services import rolling_r12 as rr

SID = "mid-x"
CUTOFF = "2026-11-02"


def test_trades_sold_after_cutoff_excluded_from_verdict(monkeypatch, tmp_path):
    monkeypatch.setattr(ms, "STRATEGIES", {SID: {"name": "测试", "horizon": "mid"}})
    monkeypatch.setattr(ms, "_hold", lambda sid: 20)
    # 窗口内 30 笔截止日前卖出、每笔 +1%；另 5 笔截止日后才卖出、每笔 −20%；一笔信号在窗口外
    batches = {
        "a.json": {"strategy_id": SID, "signal_date": "2026-06-01",
                   "picks": [{"status": "closed", "ret": 1.0, "exit_date": "2026-07-01"}] * 29
                   + [{"status": "closed", "ret": 1.0, "exit_date": CUTOFF}]},  # 卖出日 = cutoff 要算
        "b.json": {"strategy_id": SID, "signal_date": "2026-10-20",
                   "picks": [{"status": "closed", "ret": -20.0, "exit_date": "2026-11-17"}] * 5
                   + [{"status": "holding", "ret": 3.0}]},
        "c.json": {"strategy_id": SID, "signal_date": "2025-10-01",
                   "picks": [{"status": "closed", "ret": -50.0, "exit_date": "2025-11-01"}]},
    }
    for name, b in batches.items():
        (tmp_path / name).write_text(json.dumps(b), encoding="utf-8")
    monkeypatch.setattr(ms, "_load_batch", lambda db, f: json.loads(f.read_text(encoding="utf-8")))

    out = rr.midlong_window_stats(None, tmp_path, "2025-11-02", CUTOFF)[SID]

    assert out["n"] == 30 and out["avg"] == 1.0 and out["verdict"] == "positive"
    assert out["open"] == 6  # 5 笔截止日后卖出 + 1 笔持有中
    assert out["sold_after_cutoff"] == {"n": 5, "avg": -20.0}
