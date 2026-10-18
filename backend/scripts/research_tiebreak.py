"""Sprint4 §85：同分平手规则（只在 final_score 完全相同时生效，不改分数排序）。
预注册变体：
  base : 现行（代码升序）
  turn : 换手率低者优先
  rs20 : 20 日相对强度（primary_excess20）高者优先
  mv   : 流通市值（free_mv_yi）小者优先
对照：20 次 hash 随机平手（seed 0..19）的分布。
采纳标准（预设）：4 窗（12m/EXT/W0/W00）中 ≥3 窗每笔均值高于随机分布中位数，且 12m 每笔均值与 PF 不低于 base。
出场按当前 exit_overrides 逐票重模拟（与 §84 口径一致）。
"""
from __future__ import annotations
import argparse, hashlib, json, os, statistics, sys
from collections import defaultdict
from pathlib import Path
os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.db.session import SessionLocal
from app.engine.production_policy import load_production_selection_policy
from app.services.standalone_strategies import standalone_selection_policy
from app.services.backtest.policy_experiment import _simulate_card
from scripts.research_entry_rule_panel import RULES, _metrics, run_rule
from scripts.research_round2_single_skill_eval import _register_only
from scripts.research_stability_gates import panels
from scripts.research_exit_resim import fetch_bars, variant, Day
from scripts.research_trend_regime_gate import LIVE_DAY, CARD

LIVE_DAY = dict(LIVE_DAY); LIVE_DAY.setdefault("low-max-steady-trend", lambda ms: False)
EPS = 1e-6

def fv(c, k):
    v = (c.get("feature_snapshot") or {}).get(k)
    if v is None: v = c.get(k)
    try: return float(v)
    except (TypeError, ValueError): return None

def keyfn(name, date):
    if name == "turn": return lambda c: (fv(c, "turnover_rate") if fv(c, "turnover_rate") is not None else 1e9)
    if name == "rs20": return lambda c: -(fv(c, "primary_excess20") if fv(c, "primary_excess20") is not None else -1e9)
    if name == "eff20": return lambda c: -(fv(c, "eff20") if fv(c, "eff20") is not None else -1e9)
    if name == "mv": return lambda c: (fv(c, "free_mv_yi") if fv(c, "free_mv_yi") is not None else 1e9)
    if name.startswith("rnd"):
        s = name[3:]; return lambda c: hashlib.md5(f"{s}|{date}|{c.get('code')}".encode()).hexdigest()
    return None

def tiebreak(cs, name, date):
    if name.startswith("pool"):  # §93: random pick among cards already passing the score line (pool control)
        sd = name[4:]
        for c in cs:
            if float(c.get("final_score") or 0) >= 72.0:
                h = int(hashlib.md5(f"{sd}|{date}|{c.get('code')}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
                c["final_score"] = 72.0 + 20.0 * h
        return cs
    k = keyfn(name, date)
    if k is None: return cs
    groups = defaultdict(list)
    for c in cs: groups[float(c.get("final_score") or 0)].append(c)
    for sc, g in groups.items():
        if len(g) < 2: continue
        g.sort(key=lambda c: (k(c), str(c.get("code"))))
        n = len(g)
        for i, c in enumerate(g):
            c["final_score"] = sc + EPS * (n - 1 - i)
    return cs

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sids", default="chao-gu-yang-jia-skill,sector-relative-strength-leader,low-max-steady-trend,chen-xiaoqun-skill")
    ap.add_argument("--nrand", type=int, default=20)
    ap.add_argument("--variants", default="turn,rs20,mv")
    ap.add_argument("--out", default="output/research/sprint4/tiebreak.json")
    a = ap.parse_args(); base_pol = load_production_selection_policy(); res = {}
    ep = variant(); lim = ep.entry_lookahead_days + ep.horizon_days + 2
    ap_pool = os.environ.get("TIEBREAK_POOL") == "1"
    names = ["base"] + [v for v in a.variants.split(",") if v] + [f"{'pool' if ap_pool else 'rnd'}{i}" for i in range(a.nrand)]
    with SessionLocal() as db:
        for sid in a.sids.split(","):
            pol = standalone_selection_policy(base_pol, sid, 3); res[sid] = {}
            for w, f in panels(sid).items():
                p = json.loads(f.read_text()); _register_only(p)
                days = [d["signal_date"] for d in p["days"]]
                cards = {(d["signal_date"], c["code"]): c for d in p["days"] for c in d["cards"]}
                base = RULES[f"only:{sid}"]; out = {}; allrows = {}
                for v in names:
                    def rule(cs, d, _v=v):
                        if LIVE_DAY[sid](d["market_snapshot"]): return []
                        cs = base(cs, d)
                        if sid in CARD: cs = [c for c in cs if CARD[sid](c)]
                        return tiebreak(cs, _v, d["signal_date"])
                    _, rows = run_rule(rule, p, pol, {"output_count": 3, "score_line": 72.0}, apply_global_weights=False)
                    allrows[v] = rows
                need = defaultdict(set)
                for rows in allrows.values():
                    for r in rows: need[r["dt"]].add(r["code"])
                bars = fetch_bars(db, need); cache = {}
                def net(dt, code):
                    if (dt, code) not in cache:
                        day = Day(); day.signal_date = dt; day.outcome_bars = {code: tuple(bars[dt].get(code, ())[:lim])}
                        tr = (_simulate_card(day, cards[(dt, code)], ep) or {}).get("trade")
                        cache[(dt, code)] = float(tr.net_return_pct) if tr and tr.executed else None
                    return cache[(dt, code)]
                for v, rows in allrows.items():
                    nrows = [{"dt": r["dt"], "code": r["code"], "net": x} for r in rows if (x := net(r["dt"], r["code"])) is not None]
                    m = _metrics(nrows, days)
                    out[v] = {"n": m["n"], "wr": round(m["wr"] * 100, 1), "avg": round(m["avg"], 3), "pf": round(m["pf"], 2), "mdd": round(m["mdd"], 1)} if m else {"n": 0, "avg": 0}
                rnd = [out[f"{'pool' if ap_pool else 'rnd'}{i}"]["avg"] for i in range(a.nrand)]
                out["_rnd"] = {"median": round(statistics.median(rnd), 3), "p10": round(sorted(rnd)[int(0.1 * len(rnd))], 3), "p90": round(sorted(rnd)[int(0.9 * len(rnd)) - 1], 3), "min": min(rnd), "max": max(rnd)}
                res[sid][w] = out
                print(sid, w, {k: out[k] for k in ["base"] + [v for v in a.variants.split(",") if v] + ["_rnd"]}, flush=True)
                Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1))
if __name__ == "__main__":
    main()
