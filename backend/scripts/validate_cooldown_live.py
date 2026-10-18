"""§104：核对线上冷却模块（app/services/shortline_cooldown.py，种子+日K≤T 重模拟）与研究 C3（base 选股）逐日一致。
只读（不写库、不写影子文件）。用法：python scripts/validate_cooldown_live.py [--from 2026-01-20]
"""
from __future__ import annotations
import argparse, json, os, sys
from collections import defaultdict
from pathlib import Path

os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

import app.services.backtest.policy_experiment as PE
from sqlalchemy import text
from app.db.session import SessionLocal
from app.engine.production_policy import load_production_selection_policy
from app.services.standalone_strategies import standalone_selection_policy
from app.services import shortline_cooldown as SC
from scripts.research_entry_rule_panel import RULES, run_rule
from scripts.research_round2_single_skill_eval import _register_only
from scripts.research_stability_gates import panels
from scripts.research_exit_resim import fetch_bars, variant, Day
from scripts.research_tiebreak import LIVE_DAY, tiebreak
from scripts.research_trend_regime_gate import CARD
from scripts.research_cooldown_filter import blocked_days, _d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sid", default="sector-relative-strength-leader")
    ap.add_argument("--from", dest="frm", default="2026-01-20")
    a = ap.parse_args()
    sid = a.sid
    pol = standalone_selection_policy(load_production_selection_policy(), sid, 3)
    ep = variant(); lim = ep.entry_lookahead_days + ep.horizon_days + 2
    with SessionLocal() as db:
        cal8 = sorted({_d(r[0]) for r in db.execute(text("SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001'"))})
        shadow, days12 = [], []
        for w in ("EXT", "12m"):
            p = json.loads(panels(sid)[w].read_text()); _register_only(p)
            cards = {(d["signal_date"], c["code"]): c for d in p["days"] for c in d["cards"]}
            base = RULES[f"only:{sid}"]

            def rule(cs, d):
                if LIVE_DAY[sid](d["market_snapshot"]):
                    return []
                cs = base(cs, d)
                if sid in CARD:
                    cs = [c for c in cs if CARD[sid](c)]
                return tiebreak(cs, "base", d["signal_date"])
            _, rows = run_rule(rule, p, pol, {"output_count": 3, "score_line": 72.0}, apply_global_weights=False)
            need = defaultdict(set)
            for r in rows:
                need[r["dt"]].add(r["code"])
            bars = fetch_bars(db, need)
            for r in rows:
                day = Day(); day.signal_date = r["dt"]; day.outcome_bars = {r["code"]: tuple(bars[r["dt"]].get(r["code"], ())[:lim])}
                tr = (PE._simulate_card(day, cards[(r["dt"], r["code"])], ep) or {}).get("trade")
                if tr and tr.executed and tr.exit_date:
                    shadow.append((_d(tr.exit_date), _d(tr.entry_date), r["code"], float(tr.net_return_pct)))
            if w == "12m":
                days12 = [d["signal_date"] for d in p["days"]]
        shadow.sort()
        res_blk = blocked_days(shadow, cal8)["C3"]
        tdi = {d: i for i, d in enumerate(cal8)}
        agree = dis = 0
        bad = []
        for t in days12:
            if t < a.frm:
                continue
            st = SC.cooldown_state(db, sid, t)
            r = tdi[_d(t)] in res_blk
            if bool(st["active"]) == r:
                agree += 1
            else:
                dis += 1; bad.append((t, r, st))
        print(f"{sid}: days {agree + dis} agree {agree} disagree {dis}")
        for b in bad[:20]:
            print("  DIFF", b)
        st = SC.cooldown_state(db, sid, "2026-10-16")
        print("state 2026-10-16:", st)


if __name__ == "__main__":
    main()
