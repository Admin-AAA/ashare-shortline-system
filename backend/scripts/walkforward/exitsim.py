import json, math, sys
sys.path.insert(0, ".")
sys.path.insert(0, "scripts/walkforward")
from app.engine.backtest.contracts import BarInput, RecommendationInput
from app.engine.simulation import simulate
from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY as EP
from app.services.entry_policy import execution_policy_with_entry_mode
from harness import load
days = {d["signal_date"]: d for d in load("output/research/stage5/panel6/entry_panel_WX.json")["days"]}
BARS = json.load(open(".cache/wf/bars.json"))
CARD = {(dt, str(c["code"])): c for dt, d in days.items() for c in d["cards"]}

def entry_mode(card, how):
    sigs = [s for s in card.get("strategy_signals") or [] if not s.get("hard_veto")]
    if not sigs: return None
    if how == "top":
        s = max(sigs, key=lambda s: float(s.get("score") or 0) * float(s.get("weight") or 1))
    else:
        s = max(sigs, key=lambda s: float(s.get("score") or 0))
    return s.get("entry_mode")

def sim(dt, code, policy, how="top"):
    card = CARD[(dt, code)]
    bars = []
    for b in BARS.get(dt, {}).get(code, []):
        try:
            bars.append(BarInput(code=code, trade_date=b[0], open=b[1], high=b[2], low=b[3], close=b[4], volume=b[5], tradable=bool((b[5] or 0) > 0)))
        except Exception:
            pass
    if not bars: return None
    price = float(card.get("recommend_price") or 0)
    if price <= 0: return None
    pol = execution_policy_with_entry_mode(policy, entry_mode(card, how), legacy_strategy_id=card.get("bucket"))
    t = simulate(RecommendationInput(code=code, signal_date=dt, signal_price=price), tuple(bars), pol).trade
    return float(t.net_return_pct) if t is not None and t.executed else None

if __name__ == "__main__":
    for how in ("top", "max"):
        ok = tot = 0
        for dt, d in days.items():
            for c in d["selected"]:
                ref = (d.get("net_by_code") or {}).get(c)
                v = sim(dt, c, EP, how)
                tot += 1
                if (ref is None and v is None) or (ref is not None and v is not None and abs(ref - v) < 1e-6): ok += 1
        print(how, ok, tot)
