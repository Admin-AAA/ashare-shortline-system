"""[短线] §125 附表：NR7 突破各变体成交 与 线上短线 4 策略 solo 面板 base 选股（前3/线72、yaml 日否决/卡片过滤，
同 research_cooldown_filter 的 base 抽样）同日同票重叠占比。只读面板 JSON，不连库。
窗口映射：本研究 R12 ↔ 面板 12m；EXT 2025-01~09 ↔ EXT；W0 ↔ W0；W00 ↔ W00。"""
from __future__ import annotations
import json, os, sys
from pathlib import Path
os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
import polars as pl
from app.engine.production_policy import load_production_selection_policy
from app.services.standalone_strategies import standalone_selection_policy
from scripts.research_entry_rule_panel import RULES, run_rule
from scripts.research_round2_single_skill_eval import _register_only
from scripts.research_stability_gates import panels
from scripts.research_tiebreak import LIVE_DAY
from scripts.research_trend_regime_gate import CARD

SIDS = ["chen-xiaoqun-skill", "chao-gu-yang-jia-skill", "sector-relative-strength-leader", "oversold-capital-reversal"]
MAP = {"12m": "R12", "EXT": "EXT", "W0": "W0", "W00": "W00"}
S4 = B / "output/research/sprint4"


def main():
    tr = pl.concat([pl.read_csv(f, schema_overrides={"code": pl.Utf8, "dt": pl.Utf8}) for f in sorted(S4.glob("nr7_breakout*_trades_*.csv"))],
                   how="diagonal_relaxed")
    base_pol = load_production_selection_policy()
    live = {}
    for sid in SIDS:
        pol = standalone_selection_policy(base_pol, sid, 3)
        for w, f in panels(sid).items():
            if w not in MAP:
                continue
            p = json.loads(f.read_text()); _register_only(p)
            base = RULES[f"only:{sid}"]

            def rule(cs, d, _s=sid, _b=base):
                if LIVE_DAY[_s](d["market_snapshot"]):
                    return []
                cs = _b(cs, d)
                return [c for c in cs if CARD[_s](c)] if _s in CARD else cs
            _, rows = run_rule(rule, p, pol, {"output_count": 3, "score_line": 72.0}, apply_global_weights=False)
            live[(sid, w)] = {(str(r["dt"])[:10], str(r["code"])) for r in rows}
            del p
    out = {}
    for w, ph in MAP.items():
        sub = tr.filter(pl.col("phase") == ph)
        for v in sorted(set(sub["variant"].to_list())):
            keys = {(d, c) for d, c in zip(sub.filter(pl.col("variant") == v)["dt"].to_list(), sub.filter(pl.col("variant") == v)["code"].to_list())}
            row = {"n": len(keys)}
            anyl = set()
            for sid in SIDS:
                L = live.get((sid, w), set())
                hit = keys & L; anyl |= hit
                row[sid] = {"hit": len(hit), "pct_of_mine": round(100 * len(hit) / max(len(keys), 1), 1), "live_n": len(L)}
            row["any_live_pct"] = round(100 * len(anyl) / max(len(keys), 1), 1)
            out[f"{w}|{v}"] = row
            print(w, v, json.dumps(row, ensure_ascii=False), flush=True)
    (S4 / "nr7_breakout_overlap.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
