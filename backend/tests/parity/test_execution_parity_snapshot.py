"""Live/backtest execution parity regression on a fixed historical snapshot.

Fixture: real DailyKbar rows (8 codes, 2026-03-02..2026-04-15) frozen as JSON.
- Live settlement (outcome_simulation._simulate_outcome, also used by the DB
  backtest label path) must route through engine.execution_core and reproduce
  the pinned golden results exactly.
- The research adapter (engine.simulation.simulate) is pinned the same way.
Regenerate goldens only on an intentional execution-semantics change:
    PARITY_REGEN=1 pytest tests/parity
"""
import json
import os
import types
from pathlib import Path

import pytest

from app.engine import execution_core
from app.engine.backtest import BarInput, ExecutionPolicy, RecommendationInput
from app.engine.simulation import simulate
from app.models.tables import DailyKbar
from app.services import outcome_simulation as osim

HERE = Path(__file__).parent
SNAP = json.loads((HERE.parent / "fixtures" / "execution_parity_snapshot_2026Q1.json").read_text())
GOLDEN = HERE / "golden_execution_parity_2026Q1.json"
KEYS = ("entry_triggered", "entry_date", "entry_price", "exit_date", "exit_price",
        "executed_return", "net_return_pct", "exit_reason", "outcome_label", "position_size")
POLICY = ExecutionPolicy(policy_id="parity", version="1.0.0", entry_lookahead_days=1, horizon_days=5,
                         stop_loss_pct=5.0, take_profit_pct=10.0)


def _cases():
    for code, rows in sorted(SNAP.items()):
        for i in range(3, len(rows) - 6, 2):
            yield code, i


def _live():
    out = {}
    for code, i in _cases():
        bars = [DailyKbar(**r) for r in SNAP[code]]
        dates = [str(b.trade_date) for b in bars]
        rec = types.SimpleNamespace(code=code, trade_date=dates[i], recommend_price=float(bars[i].close),
                                    feature_snapshot_json="{}", bucket=None, final_score=0.0, recommended_at=None)
        r = osim._simulate_outcome(rec, bars, dates, 5) or {}
        out[f"{code}@{dates[i]}"] = {k: r.get(k) for k in KEYS}
    return out


def _research():
    out = {}
    for code, i in _cases():
        rows = SNAP[code]
        rec = RecommendationInput(code, rows[i]["trade_date"], float(rows[i]["close"]), "parity")
        bars = tuple(BarInput(code, r["trade_date"], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
                     for r in rows[i + 1:i + 7])
        t = simulate(rec, bars, POLICY).trade
        out[f"{code}@{rec.signal_date}"] = {"entry_date": t.entry_date, "exit_date": t.exit_date,
                                            "exit_reason": t.exit_reason,
                                            "gross_return_pct": round(t.gross_return_pct, 6)}
    return out


def test_parity_snapshot_matches_golden():
    current = {"live": _live(), "research": _research()}
    if os.environ.get("PARITY_REGEN"):
        GOLDEN.write_text(json.dumps(current, ensure_ascii=False, indent=1, sort_keys=True, default=str))
        pytest.skip("golden regenerated")
    golden = json.loads(GOLDEN.read_text())
    assert json.loads(json.dumps(current, default=str)) == golden


def test_live_settlement_routes_through_execution_core(monkeypatch):
    calls = []
    for name in ("resolve_causal_entry", "resolve_causal_exit"):
        orig = getattr(execution_core, name)
        def spy(*a, _o=orig, _n=name, **k):
            calls.append(_n); return _o(*a, **k)
        monkeypatch.setattr(execution_core, name, spy)
        if hasattr(osim, name):
            monkeypatch.setattr(osim, name, spy)
    _live()
    assert calls, "live settlement no longer uses engine.execution_core"
