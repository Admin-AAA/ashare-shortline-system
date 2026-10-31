"""§356 OS_RANK_PCT72：保留 final_score≥72 后按 pct/chg20 重排。预注册 docs §356。"""
from __future__ import annotations
import gc, json, os, sys
from collections import defaultdict
from pathlib import Path
os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B)); sys.path.insert(0, str(B / "scripts/midterm"))
import app.services.backtest.policy_experiment as PE
from app.db.session import SessionLocal
from app.engine.production_policy import SelectionPolicyContext, evaluate_selection_policy, load_production_selection_policy
from app.services.standalone_strategies import standalone_selection_policy
from scripts.research_entry_rule_panel import RULES
from scripts.research_round2_single_skill_eval import _register_only
from scripts.research_stability_gates import panels
from scripts.research_exit_resim import fetch_bars, variant, Day
from scripts.research_tiebreak import LIVE_DAY, tiebreak
from scripts.research_trend_regime_gate import CARD

SID, TOPN, CNAME = "oversold-capital-reversal", 3, "OS_RANK_PCT72"


def _f(c, k):
    v = (c.get("feature_snapshot") or {}).get(k)
    if isinstance(v, (int, float)): return float(v)
    v = c.get(k)
    return float(v) if isinstance(v, (int, float)) else None


def _d(x): return str(x)[:10].replace("-", "")


def os_live(c):
    p = _f(c, "pct"); return p is not None and p >= 5


def score_of(c):
    for k in ("final_score", "selection_score", "score"):
        v = c.get(k)
        if isinstance(v, (int, float)): return float(v)
    return None


def rank72(cs):
    pool = [c for c in cs if (score_of(c) is not None and score_of(c) >= 72)]
    scored = []
    for c in pool:
        p = _f(c, "pct"); chg = _f(c, "chg20")
        scored.append((p if p is not None else -999, -(chg if chg is not None else 0), c))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    out = []
    for i, (_, _, c) in enumerate(scored):
        cc = dict(c)
        cc["final_score"] = float(100 - i)
        cc["selection_score"] = float(100 - i)
        out.append(cc)
    return out


def main():
    out_dir = B / "output/research/s356"; out_dir.mkdir(parents=True, exist_ok=True)
    base_pol = load_production_selection_policy(); assert base_pol.version == 27
    ep = variant(); lim = ep.entry_lookahead_days + ep.horizon_days + 2
    res = {"BASE": {}, CNAME: {}}
    with SessionLocal() as db:
        pol = standalone_selection_policy(base_pol, SID, TOPN)
        pw = panels(SID)
        for w in ("EXT", "12m", "W0", "W00"):
            if w not in pw:
                print("MISSING", SID, w, flush=True); continue
            p = json.loads(pw[w].read_text()); _register_only(p)
            cards = {(d["signal_date"], c["code"]): c for d in p["days"] for c in d["cards"]}
            base_rule = RULES[f"only:{SID}"]
            sel = {"BASE": [], CNAME: []}; need = defaultdict(set)
            for day in p["days"]:
                if LIVE_DAY[SID](day["market_snapshot"]): continue
                cs0 = base_rule([dict(c) for c in day["cards"]], day)
                if SID in CARD: cs0 = [c for c in cs0 if CARD[SID](c)]
                cs0 = [c for c in cs0 if os_live(c)]
                cs0 = tiebreak(cs0, "base", day["signal_date"])
                pools = {"BASE": cs0, CNAME: rank72(cs0)}
                for v, cs in pools.items():
                    if not cs: continue
                    r = evaluate_selection_policy(cs, SelectionPolicyContext(
                        score_line=72.0, output_count=TOPN,
                        market_snapshot=dict(day["market_snapshot"]), risk_template="balanced",
                        same_day_industry_counts=day["same_day_industry_counts"],
                        same_day_recommendation_count=day["same_day_recommendation_count"]), pol)
                    for c in r.recommendations:
                        code = str(c.get("code")); sel[v].append((day["signal_date"], code)); need[day["signal_date"]].add(code)
            bars = fetch_bars(db, need); del p; sim = {}
            for v in ("BASE", CNAME):
                rows = []
                for k in sel[v]:
                    dt, code = k
                    seq = (bars.get(dt) or {}).get(code, ())
                    if k not in sim:
                        day = Day(); day.signal_date = dt; day.outcome_bars = {code: tuple(seq[:lim])}
                        tr = (PE._simulate_card(day, cards[k], ep) or {}).get("trade")
                        sim[k] = (float(tr.net_return_pct), _d(tr.entry_date), _d(tr.exit_date)) if tr and tr.executed and tr.exit_date else None
                    if sim[k]:
                        rows.append({"sid": SID, "dt": dt, "code": code, "net": sim[k][0], "en": sim[k][1], "ex": sim[k][2]})
                res[v][w] = rows
            print(SID, w, {v: len(res[v][w]) for v in res}, flush=True)
            del cards, bars, sim; gc.collect()
    json.dump(res, open(out_dir / "trades.json", "w"), ensure_ascii=False)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
