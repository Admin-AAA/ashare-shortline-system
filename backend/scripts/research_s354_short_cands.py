"""§354 短线三候选（预注册见 docs §354）：OS_INV_BODY_K / CGY_BREADTH / CHEN_RETREAT，各与同框架 BASE 比较。"""
from __future__ import annotations
import gc, json, os, sys
from collections import defaultdict
from pathlib import Path
os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B)); sys.path.insert(0, str(B / "scripts/midterm"))
from sqlalchemy import select
import app.services.backtest.policy_experiment as PE
from app.db.session import SessionLocal
from app.models.tables import DailyKbar
from app.engine.production_policy import SelectionPolicyContext, evaluate_selection_policy, load_production_selection_policy
from app.services.standalone_strategies import standalone_selection_policy
from scripts.research_entry_rule_panel import RULES
from scripts.research_round2_single_skill_eval import _register_only
from scripts.research_stability_gates import panels
from scripts.research_exit_resim import fetch_bars, variant, Day
from scripts.research_tiebreak import LIVE_DAY, tiebreak
from scripts.research_trend_regime_gate import CARD

TOPN = 3


def _f(c, k):
    v = (c.get("feature_snapshot") or {}).get(k)
    if isinstance(v, (int, float)): return float(v)
    v = c.get(k)
    return float(v) if isinstance(v, (int, float)) else None


def _d(x): return str(x)[:10].replace("-", "")


def os_live(c):  # 线上已合并 pct>=5
    p = _f(c, "pct"); return p is not None and p >= 5


def retreat(day):
    for c in day["cards"]:
        lu, ld = _f(c, "limit_up_count"), _f(c, "limit_down_count")
        if lu is not None and ld is not None:
            return ld >= 0.5 * lu
    return False


# sid -> (base pool filter, candidate name, candidate pool filter(card, day), entry_veto?)
CFG = {
    "oversold-capital-reversal": (os_live, "OS_INV_BODY_K", lambda c, d: os_live(c), True),
    "chao-gu-yang-jia-skill": (lambda c: True, "CGY_BREADTH", lambda c, d: (_f(c, "industry_positive_excess20_count") or -1) >= 120, False),
    "chen-xiaoqun-skill": (lambda c: True, "CHEN_RETREAT", lambda c, d: not retreat(d), False),
}


def sig_ohlc(db, keys):
    by = defaultdict(set)
    for dt, code in keys: by[dt].add(code)
    out = {}
    for dt, codes in by.items():
        for r in db.execute(select(DailyKbar.code, DailyKbar.open, DailyKbar.close).where(DailyKbar.trade_date == dt, DailyKbar.code.in_(sorted(codes)))).all():
            out[(dt, str(r.code))] = (float(r.open or 0), float(r.close or 0))
    return out


def main():
    out_dir = B / "output/research/s354"; out_dir.mkdir(parents=True, exist_ok=True)
    base_pol = load_production_selection_policy(); assert base_pol.version == 27
    ep = variant(); lim = ep.entry_lookahead_days + ep.horizon_days + 2
    res = {}
    with SessionLocal() as db:
        for sid, (bf, cname, cf, veto) in CFG.items():
            res[sid] = {"BASE": {}, cname: {}}
            pol = standalone_selection_policy(base_pol, sid, TOPN)
            pw = panels(sid)
            for w in ("EXT", "12m", "W0", "W00"):
                if w not in pw: print("MISSING", sid, w, flush=True); continue
                p = json.loads(pw[w].read_text()); _register_only(p)
                cards = {(d["signal_date"], c["code"]): c for d in p["days"] for c in d["cards"]}
                base_rule = RULES[f"only:{sid}"]
                sel = {"BASE": [], cname: []}; need = defaultdict(set)
                for day in p["days"]:
                    if LIVE_DAY[sid](day["market_snapshot"]): continue
                    cs0 = base_rule([dict(c) for c in day["cards"]], day)
                    if sid in CARD: cs0 = [c for c in cs0 if CARD[sid](c)]
                    cs0 = [c for c in cs0 if bf(c)]
                    cs0 = tiebreak(cs0, "base", day["signal_date"])
                    pools = {"BASE": cs0, cname: [c for c in cs0 if cf(c, day)]}
                    for v, cs in pools.items():
                        if not cs: continue
                        r = evaluate_selection_policy(cs, SelectionPolicyContext(score_line=72.0, output_count=TOPN,
                            market_snapshot=dict(day["market_snapshot"]), risk_template="balanced",
                            same_day_industry_counts=day["same_day_industry_counts"],
                            same_day_recommendation_count=day["same_day_recommendation_count"]), pol)
                        for c in r.recommendations:
                            code = str(c.get("code")); sel[v].append((day["signal_date"], code)); need[day["signal_date"]].add(code)
                bars = fetch_bars(db, need)
                ohlc = sig_ohlc(db, set(sel[cname])) if veto else {}
                del p; sim = {}; skipped = 0
                for v in ("BASE", cname):
                    rows = []
                    for k in sel[v]:
                        dt, code = k
                        seq = (bars.get(dt) or {}).get(code, ())
                        if v == cname and veto:
                            oc = ohlc.get(k)
                            if not oc or not seq or min(oc) <= 0: continue
                            if float(seq[0].open) < (oc[0] + oc[1]) / 2: skipped += 1; continue
                        if k not in sim:
                            day = Day(); day.signal_date = dt; day.outcome_bars = {code: tuple(seq[:lim])}
                            tr = (PE._simulate_card(day, cards[k], ep) or {}).get("trade")
                            sim[k] = (float(tr.net_return_pct), _d(tr.entry_date), _d(tr.exit_date)) if tr and tr.executed and tr.exit_date else None
                        if sim[k]: rows.append({"sid": sid, "dt": dt, "code": code, "net": sim[k][0], "en": sim[k][1], "ex": sim[k][2]})
                    res[sid][v][w] = rows
                print(sid, w, {v: len(res[sid][v][w]) for v in res[sid]}, "veto_skipped", skipped, flush=True)
                del cards, bars, sim; gc.collect()
    json.dump(res, open(out_dir / "trades.json", "w"), ensure_ascii=False)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
