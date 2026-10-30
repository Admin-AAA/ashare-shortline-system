"""§352 超跌资金反包改进：OS_PCT5 / OS_RANK_PCT / OS_INV_BODY。预注册见 docs §352。
框架同 §320：单技能、base 一次、EL_C、四窗+N3。"""
from __future__ import annotations
import gc, json, os, sys
from collections import defaultdict
from pathlib import Path

os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))
sys.path.insert(0, str(B / "scripts/midterm"))

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

SIDS = ("oversold-capital-reversal",)
NAMES = ("BASE", "OS_PCT5", "OS_RANK_PCT", "OS_INV_BODY")
TOPN = 3


def _f(c, k):
    v = (c.get("feature_snapshot") or {}).get(k)
    if isinstance(v, (int, float)):
        return float(v)
    v = c.get(k)
    return float(v) if isinstance(v, (int, float)) else None


def _pct(c):
    return _f(c, "pct") if _f(c, "pct") is not None else (float(c["pct"]) if isinstance(c.get("pct"), (int, float)) else None)


def _d(x):
    return str(x)[:10].replace("-", "")


def _ohlc(c):
    o, h, l, cl = c.get("open"), c.get("high"), c.get("low"), c.get("close")
    try:
        o, h, l, cl = float(o), float(h), float(l), float(cl)
    except (TypeError, ValueError):
        return None
    if min(o, h, l, cl) <= 0:
        return None
    return o, h, l, cl


def rank_pct(cs):
    scored = []
    for c in cs:
        p = _pct(c)
        chg = _f(c, "chg20")
        scored.append((p if p is not None else -999, -(chg if chg is not None else 0), c))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    out = []
    for i, (_, _, c) in enumerate(scored):
        cc = dict(c)
        cc["final_score"] = float(100 - i)
        cc["selection_score"] = float(100 - i)
        out.append(cc)
    return out


def entry_open(bars_for_day, code):
    seq = bars_for_day.get(code) or ()
    if not seq:
        return None
    o = float(seq[0].open or 0)
    return o if o > 0 else None


def main():
    out_dir = Path(os.environ.get("S352_OUT", str(B / "output/research/s352")))
    out_dir.mkdir(parents=True, exist_ok=True)
    base_pol = load_production_selection_policy()
    assert base_pol.version == 27, base_pol.version
    ep = variant()
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    keep = {}
    with SessionLocal() as db:
        for w in ("EXT", "12m", "W0", "W00"):
            picks = {v: [] for v in NAMES}
            days_by_sid = {}
            for sid in SIDS:
                pw = panels(sid)
                if w not in pw:
                    print("MISSING", sid, w, flush=True)
                    continue
                pol = standalone_selection_policy(base_pol, sid, TOPN)
                p = json.loads(pw[w].read_text())
                _register_only(p)
                days_by_sid[sid] = [d["signal_date"] for d in p["days"]]
                cards = {(d["signal_date"], c["code"]): c for d in p["days"] for c in d["cards"]}
                base_rule = RULES[f"only:{sid}"]
                # collect selected codes per variant (before sim)
                rows_by = {v: [] for v in NAMES}
                need = defaultdict(set)
                for day in p["days"]:
                    if LIVE_DAY[sid](day["market_snapshot"]):
                        continue
                    cs0 = base_rule([dict(c) for c in day["cards"]], day)
                    if sid in CARD:
                        cs0 = [c for c in cs0 if CARD[sid](c)]
                    cs0 = tiebreak(cs0, "base", day["signal_date"])
                    # variant pools / ranks
                    pools = {
                        "BASE": cs0,
                        "OS_PCT5": [c for c in cs0 if (_pct(c) is not None and _pct(c) >= 5)],
                        "OS_RANK_PCT": rank_pct(cs0),
                        "OS_INV_BODY": cs0,
                    }
                    for v, cs in pools.items():
                        if not cs:
                            continue
                        r = evaluate_selection_policy(
                            cs,
                            SelectionPolicyContext(
                                score_line=72.0,
                                output_count=TOPN,
                                market_snapshot=dict(day["market_snapshot"]),
                                risk_template="balanced",
                                same_day_industry_counts=day["same_day_industry_counts"],
                                same_day_recommendation_count=day["same_day_recommendation_count"],
                            ),
                            pol,
                        )
                        for c in r.recommendations:
                            code = str(c.get("code"))
                            rows_by[v].append({"sid": sid, "dt": day["signal_date"], "code": code, "variant": v})
                            need[day["signal_date"]].add(code)
                bars = fetch_bars(db, need)
                del p
                sim = {}
                for v in NAMES:
                    for r in rows_by[v]:
                        k = (r["dt"], r["code"])
                        card = cards[k]
                        # INV_BODY entry skip
                        if v == "OS_INV_BODY":
                            oh = _ohlc(card)
                            nxt = entry_open(bars.get(r["dt"]) or {}, r["code"])
                            if oh is None or nxt is None:
                                continue
                            body_mid = (oh[0] + oh[3]) / 2
                            if nxt < body_mid:
                                continue
                        if k not in sim:
                            day = Day()
                            day.signal_date = r["dt"]
                            day.outcome_bars = {r["code"]: tuple((bars.get(r["dt"]) or {}).get(r["code"], ())[:lim])}
                            tr = (PE._simulate_card(day, card, ep) or {}).get("trade")
                            sim[k] = (
                                (float(tr.net_return_pct), _d(tr.entry_date), _d(tr.exit_date))
                                if tr and tr.executed and tr.exit_date
                                else None
                            )
                        s = sim[k]
                        if not s:
                            continue
                        picks[v].append({"sid": r["sid"], "dt": r["dt"], "code": r["code"], "net": s[0], "en": s[1], "ex": s[2]})
                del cards, bars, sim, rows_by
                gc.collect()
            keep[w] = {"days_by_sid": days_by_sid, "tr": {v: picks[v] for v in NAMES}}
            print(w, "done", {v: len(picks[v]) for v in NAMES}, flush=True)
            del picks
            gc.collect()
    keep["N3"] = {
        "tr": {v: keep["12m"]["tr"][v] + keep["EXT"]["tr"][v] for v in NAMES},
    }
    out_p = out_dir / "trades.json"
    json.dump({v: {w: keep[w]["tr"][v] for w in ("12m", "EXT", "W0", "W00")} for v in NAMES}, open(out_p, "w"), ensure_ascii=False)
    print("DONE", out_p, flush=True)


if __name__ == "__main__":
    main()
