"""Sprint3 §71 筛查：均线破位出场（收盘跌破 MA5/MA10 当日收盘卖）+ 放宽跟踪/加长持有。
近似：先用真实 _simulate_card 跑底层出场口径，再在 [入场次日, 原离场日) 内若某日收盘 < MAk 则改为该日收盘离场
（只会提前离场；费用沿用原交易 gross−net）。只做筛查，采用前须在 execution_core 实现并真回放。
"""
from __future__ import annotations
import argparse, json, os, sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.tables import DailyKbar
import app.services.backtest.policy_experiment as pe
from app.engine.production_policy import load_production_selection_policy
from app.services.standalone_strategies import standalone_selection_policy
from scripts.research_entry_rule_panel import RULES, _metrics, run_rule
from scripts.research_round2_single_skill_eval import _register_only
from scripts.research_stability_gates import panels, VARIANTS2, gate
from scripts.research_exit_resim import variant, fetch_bars, Day, GRID

def closes(db, code, d0, d1):
    rows = db.execute(select(DailyKbar.trade_date, DailyKbar.close).where(DailyKbar.code == code, DailyKbar.trade_date >= d0, DailyKbar.trade_date <= d1).order_by(DailyKbar.trade_date)).all()
    return [(str(r[0]), float(r[1] or 0)) for r in rows]

VARS = {"cur": ("cur", None), "cur_ma5": ("cur", 5), "cur_ma10": ("cur", 10),
        "hz10_ma5": ("hz10", 5), "hz10_ma10": ("hz10", 10),
        "off_hz10_ma5": ("trail_off_hz10", 5), "off_hz10_ma10": ("trail_off_hz10", 10),
        "act3_r03_hz10_ma5": ("act3_r03_hz10", 5), "act3_r03_hz10_ma10": ("act3_r03_hz10", 10)}
GRID["trail_off_hz10"] = {"trail_off": True, "hz": 10}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--sid", required=True); ap.add_argument("--gate", default="base"); ap.add_argument("--out", required=True)
    a = ap.parse_args(); base_pol = load_production_selection_policy(); res = {}
    with SessionLocal() as db:
        pol = standalone_selection_policy(base_pol, a.sid, 3)
        for w, f in panels(a.sid).items():
            p = json.loads(f.read_text()); _register_only(p); days = [d["signal_date"] for d in p["days"]]
            base = RULES[f"only:{a.sid}"]; pred = VARIANTS2.get(a.gate)
            rule = base if pred is None else (lambda c, d, _b=base, _g=gate(pred): _g(_b(c, d), d))
            _, rows = run_rule(rule, p, pol, {"output_count": 3, "score_line": 72.0}, apply_global_weights=False)
            cards = {(d["signal_date"], c["code"]): c for d in p["days"] for c in d["cards"]}
            by_day = defaultdict(set)
            for r in rows: by_day[r["dt"]].add(r["code"])
            bars = fetch_bars(db, by_day); hist = {}
            out = {}
            for vname, (gname, k) in VARS.items():
                kw = {} if gname == "cur" else GRID[gname]
                ep = variant(**kw); lim = ep.entry_lookahead_days + ep.horizon_days + 2; nrows = []
                for r in rows:
                    day = Day(); day.signal_date = r["dt"]; day.outcome_bars = {r["code"]: tuple(bars[r["dt"]].get(r["code"], ())[:lim])}
                    tr = (pe._simulate_card(day, cards[(r["dt"], r["code"])], ep) or {}).get("trade")
                    if not (tr and tr.executed): continue
                    net = float(tr.net_return_pct)
                    if k and tr.entry_date and tr.exit_date:
                        key = (r["code"], r["dt"])
                        if key not in hist:
                            d0 = (date.fromisoformat(r["dt"]) - timedelta(days=40)).isoformat(); d1 = (date.fromisoformat(r["dt"]) + timedelta(days=40)).isoformat()
                            hist[key] = closes(db, r["code"], d0, d1)
                        cs = hist[key]; ds = [x[0] for x in cs]
                        ed, xd = str(tr.entry_date), str(tr.exit_date)
                        cost = float(tr.gross_return_pct) - net
                        for i, (dd, c) in enumerate(cs):
                            if dd <= ed or dd >= xd or i < k: continue
                            ma = sum(x[1] for x in cs[i - k + 1:i + 1]) / k
                            if c < ma:
                                net = (c / float(tr.entry_price) - 1) * 100 - cost; break
                    nrows.append({"dt": r["dt"], "code": r["code"], "net": net})
                m = _metrics(nrows, days)
                out[vname] = {"n": m["n"], "avg": round(m["avg"], 2), "pf": round(m["pf"], 2), "mdd": round(m["mdd"], 1)} if m else {"n": 0}
            res[w] = out; print(a.sid, w, out["cur"], flush=True)
            Path(a.out).write_text(json.dumps({a.sid: res}, ensure_ascii=False, indent=1))
if __name__ == "__main__":
    main()
