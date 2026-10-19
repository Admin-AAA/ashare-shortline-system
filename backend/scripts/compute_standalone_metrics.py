"""单策略口径指标落盘：run_rule(only:<sid>) + 生产选股政策（含 hard_veto/风控/高潮闸门），每日前 3、线 72。
输出 data/strategy_metrics/standalone_metrics.json，供 /api/strategies/standalone-metrics 读取。
用法：python scripts/compute_standalone_metrics.py [--panel output/research/round3/panel/entry_panel_WX.json] [--split 2026-05-01]
调参后的过滤（L-172）在面板上按 feature_snapshot 套用，待新面板生成后会自然包含。
"""
from __future__ import annotations
import argparse, json, sys
from datetime import datetime
from pathlib import Path
B = Path(__file__).resolve().parents[1]
import os
# 研究回放会导出 ASHARE_RESEARCH_EXTRA_SKILLS；它会改变聚合/政策口径，落盘给策略页的指标必须按生产口径算（L-180）。
os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
sys.path.insert(0, str(B))
from app.engine.production_policy import load_production_selection_policy
import scripts.research_entry_rule_panel as mod
from scripts.research_entry_rule_panel import RULES, _metrics, run_rule
from scripts.research_round2_single_skill_eval import _register_only

OUT = B / "data" / "strategy_metrics" / "standalone_metrics.json"

def _fs(c, k):
    v = (c.get("feature_snapshot") or {}).get(k)
    return v if v is not None else c.get(k)

def _extra(sid):
    # 所有调参后的过滤都已写进 strategy.yaml，真单策略面板按新 yaml 重放，无需再模拟（L-186）。
    return None

def _cooldown_rows(db, sid, rows, panel):
    """§104：已采纳冷却规则的策略（app/services/shortline_cooldown.COOLDOWN_RULES）在页面指标里按线上口径剔除冷却日。
    影子单 = EXT 面板（若有）+ 本面板的未过滤选股；出场日用 _simulate_card 重模拟；严格 PIT（exit ≤ T）。"""
    from collections import defaultdict
    from sqlalchemy import text
    import app.services.backtest.policy_experiment as PE
    from app.services.shortline_cooldown import equity_ma_blocked
    from scripts.research_exit_resim import fetch_bars, variant, Day
    from scripts.research_stability_gates import panels
    ep = variant(); lim = ep.entry_lookahead_days + ep.horizon_days + 2
    hist = []
    srcs = []
    ext = panels(sid).get("EXT")
    if ext is not None:
        pe = json.loads(ext.read_text()); _register_only(pe)
        _, er = run_rule(RULES[f"only:{sid}"], pe, standalone_selection_policy_cached(sid), apply_global_weights=False)
        srcs.append((pe, er))
    srcs.append((panel, rows))
    for p, rr in srcs:
        cards = {(d["signal_date"], c["code"]): c for d in p["days"] for c in d["cards"]}
        need = defaultdict(set)
        for r in rr:
            need[r["dt"]].add(r["code"])
        bars = fetch_bars(db, need)
        for r in rr:
            day = Day(); day.signal_date = r["dt"]; day.outcome_bars = {r["code"]: tuple(bars[r["dt"]].get(r["code"], ())[:lim])}
            tr = (PE._simulate_card(day, cards[(r["dt"], r["code"])], ep) or {}).get("trade")
            if tr and tr.executed and tr.exit_date:
                hist.append((str(tr.exit_date)[:10], float(tr.net_return_pct)))
    cal = sorted({str(x[0])[:10] for x in db.execute(text("SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001'"))})
    days = sorted({r["dt"] for r in rows})
    blocked = {d for d in days if equity_ma_blocked(hist, cal, d)["active"]}
    return [r for r in rows if r["dt"] not in blocked], len(blocked)


_POL_CACHE = {}
def standalone_selection_policy_cached(sid):
    from app.services.standalone_strategies import standalone_selection_policy
    if sid not in _POL_CACHE:
        _POL_CACHE[sid] = standalone_selection_policy(load_production_selection_policy(), sid, 3)
    return _POL_CACHE[sid]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="", help="默认取 output/research/round*/panel 下最新的面板")
    ap.add_argument("--split", default="2026-05-01")
    a = ap.parse_args()
    if not a.panel:
        cands = sorted(B.glob("output/research/round*/panel/entry_panel_WX.json"), key=lambda x: x.stat().st_mtime)
        if not cands:
            print("no panel"); return
        a.panel = str(cands[-1])
    from app.db.session import SessionLocal
    from app.services.workbench_service import sync_skills
    from app.services.standalone_strategies import WATCH_STRATEGY_IDS, standalone_strategy_ids
    with SessionLocal() as db:
        skills = sync_skills(db)
        ids = standalone_strategy_ids(skills)
        names = {s.skill_id: s.name for s in skills}
    from app.services.standalone_strategies import standalone_selection_policy
    shared = json.loads(Path(a.panel).read_text())
    base_pol = load_production_selection_policy(); _register_only(shared)
    mod.OUTPUT_COUNT, mod.SCORE_LINE = 3, 72.0
    out = []
    sources = {}
    for sid in ids:
        # 优先用真单策略面板（其它策略全部 exclude，无跨策略共振/共识加分，L-181）；没有时退回共享面板。
        solo = B / "output" / "research" / "solo" / sid / "panel" / "entry_panel_WX.json"
        solo_ok = solo.exists() and (solo.parent.parent / "replay.exit").exists() and (solo.parent.parent / "replay.exit").read_text().strip() == "0"
        p = json.loads(solo.read_text()) if solo_ok else shared
        if solo_ok:
            _register_only(p)
        sources[sid] = "solo" if solo_ok else Path(a.panel).parent.parent.name
        pol = standalone_selection_policy(base_pol, sid, 3)
        days = [d["signal_date"] for d in p["days"]]
        base = RULES.get(f"only:{sid}")
        if base is None:
            out.append({"strategy_id": sid, "name": names.get(sid, sid), "status": "no_data"}); continue
        ex = _extra(sid)
        rule = base if ex is None else (lambda cards, d, _b=base, _e=ex: [c for c in _b(cards, d) if _e(c)])
        _, rows = run_rule(rule, p, pol, apply_global_weights=False)
        cooldown = None
        from app.services.shortline_cooldown import COOLDOWN_RULES
        if sid in COOLDOWN_RULES:
            with SessionLocal() as _db:
                rows, _nb = _cooldown_rows(_db, sid, rows, p)
            cooldown = {"rule": COOLDOWN_RULES[sid], "blocked_signal_days": _nb}
        def m(lo, hi):
            r = _metrics([x for x in rows if lo <= x["dt"] < hi], [x for x in days if lo <= x < hi])
            if r is None: return {"n": 0}
            return {"n": int(r["n"]), "win_rate": round(r["wr"] * 100, 1), "avg_return_pct": round(r["avg"], 2),
                    "pf": round(r["pf"], 2) if r["pf"] is not None else None, "max_drawdown_pct": round(r["mdd"], 1)}
        w12, wr = m("0", "9"), m(a.split, "9")
        status = "positive" if (w12.get("avg_return_pct") or 0) > 0 and (wr.get("avg_return_pct") or 0) > 0 else "watch"
        if sid in WATCH_STRATEGY_IDS: status = "watch"
        out.append({"strategy_id": sid, "name": names.get(sid, sid), "status": status, "last_12m": w12, "recent": wr,
                    "tuned_filter_emulated": ex is not None, "panel": sources[sid],
                    **({"cooldown_emulated": cooldown} if cooldown else {})})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "window": {"start": shared["days"][0]["signal_date"], "end": shared["days"][-1]["signal_date"], "recent_start": a.split},
        "method": f"单策略政策（production_selection.v{base_pol.version} 去组合共识闸门），每日前 3，分数线 72；面板 solo=真单策略回放，否则 {Path(a.panel).parent.parent.name}",
        "strategies": out}, ensure_ascii=False, indent=1))
    print(OUT.read_text())

if __name__ == "__main__":
    main()
