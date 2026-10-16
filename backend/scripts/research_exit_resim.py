"""Sprint3：在单策略面板选中的票（前3/线72）上，用真实 _simulate_card 重算不同出场参数。
先校验 base 出场与面板 net 一致，再跑网格。
"""
from __future__ import annotations
import argparse, dataclasses as dc, json, os, sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.tables import DailyKbar
from app.engine.backtest import BarInput
from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY as EP
from app.engine.production_policy import load_production_selection_policy
from app.services.standalone_strategies import standalone_selection_policy
from app.services.backtest.policy_experiment import _simulate_card
from scripts.research_entry_rule_panel import RULES, _metrics, run_rule
from scripts.research_round2_single_skill_eval import _register_only
from scripts.research_stability_gates import panels, VARIANTS2, gate

def variant(**kw):
    ep = EP
    if "tp" in kw: ep = dc.replace(ep, take_profit=dc.replace(ep.take_profit, enabled=True, default_gain_pct=kw["tp"], maximum_gain_pct=max(kw["tp"], ep.take_profit.maximum_gain_pct)))
    tr = {}
    for k, f in (("act", "activation_gain_pct"), ("cb", "callback_pct"), ("lock", "minimum_lock_gain_pct"), ("ratio", "profit_lock_ratio")):
        if k in kw: tr[f] = kw[k]
    if "trail_off" in kw: tr["enabled"] = False
    if tr: ep = dc.replace(ep, trailing=dc.replace(ep.trailing, **tr))
    if "hz" in kw: ep = dc.replace(ep, horizon_days=kw["hz"])
    if "sl" in kw: ep = dc.replace(ep, stop_loss=dc.replace(ep.stop_loss, default_loss_pct=kw["sl"], maximum_loss_pct=max(kw["sl"] + 0.2, ep.stop_loss.maximum_loss_pct)))
    return ep

GRID = {
    "base": {},
    "act2_cb2": {"act": 2.0, "cb": 2.0},
    "act3_cb3": {"act": 3.0, "cb": 3.0},
    "act3_cb3_lock1": {"act": 3.0, "cb": 3.0, "lock": 1.0},
    "act5_cb4": {"act": 5.0, "cb": 4.0},
    "act5_cb4_hz8": {"act": 5.0, "cb": 4.0, "hz": 8},
    "act3_cb3_hz8": {"act": 3.0, "cb": 3.0, "hz": 8},
    "ratio0.3": {"ratio": 0.3},
    "ratio0.7": {"ratio": 0.7},
    "cb2": {"cb": 2.0},
    "cb1.5": {"cb": 1.5},
    "act2": {"act": 2.0}, "act3": {"act": 3.0}, "act4": {"act": 4.0}, "act3_r03": {"act": 3.0, "ratio": 0.3}, "r03": {"ratio": 0.3}, "r04": {"ratio": 0.4},
    "lock1": {"lock": 1.0},
    "lock4": {"lock": 4.0}, "lock3": {"lock": 3.0}, "lock5": {"lock": 5.0},
    "hz3": {"hz": 3}, "hz7": {"hz": 7}, "hz10": {"hz": 10},
    "trail_off": {"trail_off": True},
    "trail_off_tp8": {"trail_off": True, "tp": 8.0},
    "tp10": {"tp": 10.0},
    "sl4": {"sl": 4.0}, "sl7": {"sl": 7.0},
}

def fetch_bars(db, by_day, hz_max=12):
    out = {}
    for d, codes in by_day.items():
        end = (date.fromisoformat(d) + timedelta(days=(2 + hz_max) * 3)).isoformat()
        rows = db.execute(select(DailyKbar.code, DailyKbar.trade_date, DailyKbar.open, DailyKbar.high, DailyKbar.low, DailyKbar.close, DailyKbar.volume)
                          .where(DailyKbar.code.in_(sorted(codes)), DailyKbar.trade_date > d, DailyKbar.trade_date <= end)
                          .order_by(DailyKbar.code, DailyKbar.trade_date)).all()
        g = defaultdict(list)
        for r in rows:
            pr = tuple(float(x or 0) for x in (r.open, r.high, r.low, r.close))
            if min(pr) <= 0: continue
            g[str(r.code)].append(BarInput(code=str(r.code), trade_date=str(r.trade_date), open=pr[0], high=pr[1], low=pr[2], close=pr[3],
                                           volume=float(r.volume) if r.volume is not None else None, tradable=bool(float(r.volume or 0) > 0)))
        out[d] = g
    return out

class Day: pass

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--sids", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--gate", default="base", help="VARIANTS2 里的日级闸门名，先套闸门再算出场")
    ap.add_argument("--grid", default="")
    a = ap.parse_args()
    grid = GRID if not a.grid else {k: GRID[k] for k in a.grid.split(",")}
    base_pol = load_production_selection_policy(); res = {}
    with SessionLocal() as db:
        for sid in a.sids.split(","):
            pol = standalone_selection_policy(base_pol, sid, 3); res[sid] = {}
            for w, f in panels(sid).items():
                p = json.loads(f.read_text()); _register_only(p)
                days = [d["signal_date"] for d in p["days"]]
                base = RULES[f"only:{sid}"]; pred = VARIANTS2.get(a.gate)
                rule = base if pred is None else (lambda c, d, _b=base, _g=gate(pred): _g(_b(c, d), d))
                _, rows = run_rule(rule, p, pol, {"output_count": 3, "score_line": 72.0}, apply_global_weights=False)
                cards = {(d["signal_date"], c["code"]): c for d in p["days"] for c in d["cards"]}
                by_day = defaultdict(set)
                for r in rows: by_day[r["dt"]].add(r["code"])
                bars = fetch_bars(db, by_day)
                out = {}; mism = 0
                for name, kw in grid.items():
                    ep = variant(**kw); lim = ep.entry_lookahead_days + ep.horizon_days + 2
                    nrows = []
                    for r in rows:
                        day = Day(); day.signal_date = r["dt"]; day.outcome_bars = {r["code"]: tuple(bars[r["dt"]].get(r["code"], ())[:lim])}
                        tr = (_simulate_card(day, cards[(r["dt"], r["code"])], ep) or {}).get("trade")
                        if tr and tr.executed:
                            nrows.append({"dt": r["dt"], "code": r["code"], "net": float(tr.net_return_pct)})
                            if name == "base" and abs(float(tr.net_return_pct) - r["net"]) > 1e-6: mism += 1
                        elif name == "base": mism += 1
                    m = _metrics(nrows, days)
                    out[name] = {"n": m["n"], "wr": round(m["wr"] * 100, 1), "avg": round(m["avg"], 2), "pf": round(m["pf"], 2), "mdd": round(m["mdd"], 1)} if m else {"n": 0}
                out["_base_mismatch"] = mism
                res[sid][w] = out
                print(sid, w, "mismatch", mism, out["base"], flush=True)
                Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1))
if __name__ == "__main__":
    main()
