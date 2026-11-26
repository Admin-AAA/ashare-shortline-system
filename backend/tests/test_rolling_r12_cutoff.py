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


def test_short_trades_sold_after_cutoff_excluded_from_verdict(monkeypatch):
    """短线同口径：两段（回放、实盘）都只算卖出日 ≤ cutoff 的已成交笔；截止日后才卖出的计 open、按段单列。"""
    from types import SimpleNamespace

    from app.engine.trading_costs import net_return_after_cost
    from app.services import daily_output_check as doc
    from app.services import outcome_service as os_

    monkeypatch.setattr(doc, "expected_short_strategies", lambda db: ["s1"])
    monkeypatch.setattr(rr, "short_replay_coverage_end", lambda db: ("2026-10-16", "data"))

    def row(ret, exit_date, label="success", triggered=True):
        return SimpleNamespace(outcome_label=label, entry_triggered=triggered, executed_return=ret,
                               exit_date=exit_date, skill_breakdown_json='[{"strategy_id": "s1"}]')

    def fake(db, start_date, end_date, sample_scope):
        if sample_scope == "historical_replay":
            # 25 笔早已卖出；1 笔卖出日 = cutoff 要算；1 笔截止日后才卖出（回放终点贴近截止日时会出现）
            return [row(2.0, "2026-09-01")] * 25 + [row(2.0, CUTOFF), row(-30.0, "2026-11-04")]
        # 实盘：4 笔截止日前卖出；3 笔截止日后才卖出（−20%）；1 笔没卖出日；1 笔未成交；1 笔临时态
        return ([row(2.0, "2026-10-30")] * 4 + [row(-20.0, "2026-11-05")] * 3 + [row(-20.0, None)]
                + [row(0.0, None, label="missed", triggered=False), row(9.0, "2026-10-30", label="provisional")])

    monkeypatch.setattr(os_, "_scoped_recommendation_rows", fake)

    out = rr.short_window_stats(None, "2025-11-02", CUTOFF)["strategies"]["s1"]

    net = round(net_return_after_cost(2.0), 2)
    assert out["n"] == 30 and out["avg"] == net and out["verdict"] == "positive"
    assert out["open"] == 5  # 回放 1 + 实盘 3 + 实盘没卖出日 1
    assert out["replay_segment"]["n"] == 26
    assert out["replay_segment"]["sold_after_cutoff"] == {"n": 1, "avg": round(net_return_after_cost(-30.0), 2)}
    assert out["live_segment"]["n"] == 4 and out["live_segment"]["avg"] == net
    assert out["live_segment"]["sold_after_cutoff"] == {"n": 4, "avg": round(net_return_after_cost(-20.0), 2)}
