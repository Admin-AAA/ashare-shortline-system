"""Sprint2 #1: round1 离场（持有 7 天、trailing 激活 2%）vs 基线 v5.0.0，按策略 × 市场状态 × 05-01 前后。
用法：python scripts/sprint2/exit_by_regime.py [--panel-root output/research/solo] [--out ...json]
面板 = 真单策略回放；选股 = 生产单策略政策（每日前 3、线 72），与 compute_standalone_metrics 同口径。"""
from __future__ import annotations
import argparse, dataclasses as dc, json, os, sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(B))
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.tables import DailyKbar
from app.engine.backtest.contracts import BarInput, RecommendationInput
from app.engine.simulation import simulate
from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY as EP
from app.services.entry_policy import execution_policy_with_entry_mode
from app.engine.production_policy import load_production_selection_policy
import scripts.research_entry_rule_panel as mod
from scripts.research_entry_rule_panel import RULES, run_rule
from scripts.research_round2_single_skill_eval import _register_only
from app.services.standalone_strategies import standalone_selection_policy

def variant(sl, tp, hz, act, lock, gap):
    return dc.replace(EP, horizon_days=hz,
        stop_loss=dc.replace(EP.stop_loss, default_loss_pct=sl, maximum_loss_pct=sl + 0.2),
        take_profit=dc.replace(EP.take_profit, enabled=tp is not None, default_gain_pct=tp or EP.take_profit.default_gain_pct),
        trailing=dc.replace(EP.trailing, activation_gain_pct=act, minimum_lock_gain_pct=lock),
        gap_risk=dc.replace(EP.gap_risk, max_entry_gap_up_pct=gap))
BASE = (5.5, None, 5, 0.8, 2.5, 6.0)
CAND = (5.5, None, 7, 2.0, 2.5, 6.0)
SIDS = ["chao-gu-yang-jia-skill", "chen-xiaoqun-skill", "high-range-breakout-momentum", "oversold-capital-reversal", "sector-relative-strength-leader"]

def entry_mode(card):
    sigs = [s for s in card.get("strategy_signals") or [] if not s.get("hard_veto")]
    if not sigs: return None
    return max(sigs, key=lambda s: float(s.get("score") or 0) * float(s.get("weight") or 1)).get("entry_mode")

def stats(v):
    v = [x for x in v if x is not None]
    if not v: return {"n": 0}
    g = sum(x for x in v if x > 0); l = -sum(x for x in v if x < 0)
    return {"n": len(v), "avg": round(sum(v) / len(v), 2), "wr": round(100 * sum(x > 0 for x in v) / len(v), 1), "pf": round(g / l, 2) if l else None}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--panel-root", default="output/research/solo"); ap.add_argument("--split", default="2026-05-01")
    ap.add_argument("--out", default="output/research/sprint2/exit_by_regime.json"); ap.add_argument("--sids", default=",".join(SIDS))
    a = ap.parse_args()
    base_pol = load_production_selection_policy(); mod.OUTPUT_COUNT, mod.SCORE_LINE = 3, 72.0
    pb, pc = variant(*BASE), variant(*CAND)
    db = SessionLocal(); res = {}
    for sid in a.sids.split(","):
        f = B / a.panel_root / sid / "panel" / "entry_panel_WX.json"
        if not f.exists(): print(sid, "no panel"); continue
        p = json.loads(f.read_text()); _register_only(p)
        cards = {(d["signal_date"], str(c["code"])): (c, d) for d in p["days"] for c in d["cards"]}
        _, rows = run_rule(RULES[f"only:{sid}"], p, standalone_selection_policy(base_pol, sid, 3), apply_global_weights=False)
        agg = defaultdict(lambda: {"base": [], "cand": [], "panel": []}); fid = [0, 0]
        for r in rows:
            dt, code = r["dt"], str(r["code"]); card, day = cards[(dt, code)]
            end = (date.fromisoformat(dt) + timedelta(days=30)).isoformat()
            ks = db.scalars(select(DailyKbar).where(DailyKbar.code == code, DailyKbar.trade_date > dt, DailyKbar.trade_date <= end).order_by(DailyKbar.trade_date).limit(12)).all()
            bars = tuple(BarInput(code=code, trade_date=str(k.trade_date), open=float(k.open or 0), high=float(k.high or 0), low=float(k.low or 0), close=float(k.close or 0), volume=float(k.volume) if k.volume is not None else None, tradable=bool((k.volume or 0) > 0)) for k in ks)
            price = float(card.get("recommend_price") or 0)
            out = {}
            for name, pol in (("base", pb), ("cand", pc)):
                t = simulate(RecommendationInput(code=code, signal_date=dt, signal_price=price), bars, execution_policy_with_entry_mode(pol, entry_mode(card), legacy_strategy_id=card.get("bucket"))).trade if bars and price > 0 else None
                out[name] = float(t.net_return_pct) if t is not None and t.executed else None
            ref = r.get("net")
            if ref is not None and out["base"] is not None:
                fid[1] += 1; fid[0] += abs(ref - out["base"]) < 0.05
            reg = card.get("market_regime") or "?"
            for key in (("all",), ("pre" if dt < a.split else "post",), ("reg", reg), ("pre" if dt < a.split else "post", reg)):
                k = "|".join(key)
                agg[k]["base"].append(out["base"]); agg[k]["cand"].append(out["cand"])
        res[sid] = {"fidelity_base_vs_panel": fid, "cells": {k: {"base": stats(v["base"]), "cand": stats(v["cand"])} for k, v in sorted(agg.items())}}
        print(sid, "fidelity", fid)
        for k, v in sorted(res[sid]["cells"].items()):
            print(f"  {k:18s} base {v['base']}  cand {v['cand']}")
    Path(B / a.out).parent.mkdir(parents=True, exist_ok=True); (B / a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()
