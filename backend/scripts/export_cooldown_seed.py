"""§104：导出短线冷却的影子单种子（12m 单策略面板 base 选股，口径同 research_cooldown_filter.py 的 cur/base）。
输出 data/shortline_cooldown/<sid>_seed.json：只存选股（signal_date/code/recommend_price/entry_mode），出场由
app/services/shortline_cooldown.py 按 T 时点日K 重模拟（严格 PIT）。
用法：python scripts/export_cooldown_seed.py [--sid sector-relative-strength-leader]
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path

os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from app.engine.production_policy import load_production_selection_policy
from app.services.standalone_strategies import standalone_selection_policy
from scripts.research_entry_rule_panel import RULES, run_rule
from scripts.research_round2_single_skill_eval import _register_only
from scripts.research_stability_gates import panels
from scripts.research_tiebreak import LIVE_DAY, tiebreak
from scripts.research_trend_regime_gate import CARD


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sid", default="sector-relative-strength-leader")
    a = ap.parse_args()
    sid = a.sid
    p = json.loads(panels(sid)["12m"].read_text())
    _register_only(p)
    cards = {(d["signal_date"], c["code"]): c for d in p["days"] for c in d["cards"]}
    pol = standalone_selection_policy(load_production_selection_policy(), sid, 3)
    base = RULES[f"only:{sid}"]

    def rule(cs, d):
        if LIVE_DAY[sid](d["market_snapshot"]):
            return []
        cs = base(cs, d)
        if sid in CARD:
            cs = [c for c in cs if CARD[sid](c)]
        return tiebreak(cs, "base", d["signal_date"])

    _, rows = run_rule(rule, p, pol, {"output_count": 3, "score_line": 72.0}, apply_global_weights=False)
    picks = []
    for r in rows:
        c = cards[(r["dt"], r["code"])]
        picks.append({"signal_date": r["dt"], "code": r["code"],
                      "recommend_price": float(c.get("recommend_price") or c.get("price") or c.get("close") or 0),
                      "entry_mode": c.get("entry_mode") or "target_touch", "panel_net": round(float(r["net"]), 4)})
    out = B / "data" / "shortline_cooldown" / f"{sid}_seed.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"strategy_id": sid, "source": str(panels(sid)["12m"].relative_to(B)),
                               "window": [p["days"][0]["signal_date"], p["days"][-1]["signal_date"]],
                               "method": "§104 base 选股（LIVE_DAY 日闸门 + only:<sid> + 前3/线72，平手按代码升序）",
                               "picks": picks}, ensure_ascii=False, indent=1))
    print(out, len(picks))


if __name__ == "__main__":
    main()
