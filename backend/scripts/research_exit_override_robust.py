"""Sprint4 §86：线上短线策略 exit_overrides / ma_break_exit 是否在"随机平手分布"口径下稳健占优。
对每个策略、每个窗口（12m/EXT/W0/W00），取 base（代码升序）+ 20 次 hash 随机平手的入选集合，
逐票用以下出场口径重模拟：
  cur   : 现行 yaml（exit_overrides + ma_break_exit）
  def   : 全局默认出场（关闭 overrides 与均线破位）
  ov    : 只保留 exit_overrides（关均线破位）     ——仅当策略同时有两者
  ma    : 只保留均线破位（关 overrides）          ——仅当策略同时有两者
预注册保留标准：cur 的"21 次选股平均每笔均值"在 ≥3/4 窗高于 def，且 12m 不低于 def 0.5pp 以上；
否则回退为默认出场（简化）。另报告配对胜率（21 次选股中 cur>def 的比例）。
"""
from __future__ import annotations
import argparse, json, os, statistics, sys
from collections import defaultdict
from pathlib import Path
os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
import app.services.backtest.policy_experiment as PE
from app.db.session import SessionLocal
from app.engine.production_policy import load_production_selection_policy
from app.services.standalone_strategies import standalone_selection_policy
from app.services.strategy_exit_overrides import apply_exit_overrides as _AEO
from app.services.ma_break_exit import ma_break_window_for as _MAW
from scripts.research_entry_rule_panel import RULES, _metrics, run_rule
from scripts.research_round2_single_skill_eval import _register_only
from scripts.research_stability_gates import panels
from scripts.research_exit_resim import fetch_bars, variant, Day
from scripts.research_tiebreak import LIVE_DAY, tiebreak
from scripts.research_trend_regime_gate import CARD

def set_mode(m):
    PE.apply_exit_overrides = _AEO if m in ("cur", "ov") else (lambda pol, sid: pol)
    PE.ma_break_window_for = _MAW if m in ("cur", "ma") else (lambda sid: 0)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sids", default="chao-gu-yang-jia-skill,sector-relative-strength-leader,oversold-capital-reversal")
    ap.add_argument("--nrand", type=int, default=20)
    ap.add_argument("--out", default="output/research/sprint4/exit_override_robust.json")
    a = ap.parse_args(); base_pol = load_production_selection_policy(); res = {}
    ep = variant(); lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(a.nrand)]
    with SessionLocal() as db:
        for sid in a.sids.split(","):
            has_ov = bool(PE.exit_strategy_id and __import__("app.services.strategy_exit_overrides", fromlist=["x"]).exit_overrides_for(sid))
            has_ma = bool(_MAW(sid))
            modes = ["cur", "def"] + (["ov", "ma"] if has_ov and has_ma else [])
            pol = standalone_selection_policy(base_pol, sid, 3); res[sid] = {"_has": {"ov": has_ov, "ma": has_ma}}
            for w, f in panels(sid).items():
                p = json.loads(f.read_text()); _register_only(p)
                days = [d["signal_date"] for d in p["days"]]
                cards = {(d["signal_date"], c["code"]): c for d in p["days"] for c in d["cards"]}
                base = RULES[f"only:{sid}"]; allrows = {}
                set_mode("cur")
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
                bars = fetch_bars(db, need); out = {}
                for m in modes:
                    set_mode(m); cache = {}
                    def net(dt, code):
                        if (dt, code) not in cache:
                            day = Day(); day.signal_date = dt; day.outcome_bars = {code: tuple(bars[dt].get(code, ())[:lim])}
                            tr = (PE._simulate_card(day, cards[(dt, code)], ep) or {}).get("trade")
                            cache[(dt, code)] = float(tr.net_return_pct) if tr and tr.executed else None
                        return cache[(dt, code)]
                    per = {}
                    for v, rows in allrows.items():
                        nrows = [{"dt": r["dt"], "code": r["code"], "net": x} for r in rows if (x := net(r["dt"], r["code"])) is not None]
                        mm = _metrics(nrows, days)
                        per[v] = {"n": mm["n"], "avg": round(mm["avg"], 3), "pf": round(mm["pf"], 2), "mdd": round(mm["mdd"], 1), "wr": round(mm["wr"] * 100, 1)} if mm else {"n": 0, "avg": 0, "pf": 0, "mdd": 0, "wr": 0}
                    avgs = [per[v]["avg"] for v in names]
                    out[m] = {"base": per["base"], "mean_avg": round(statistics.mean(avgs), 3), "median_avg": round(statistics.median(avgs), 3),
                              "mean_pf": round(statistics.mean(per[v]["pf"] for v in names), 2), "mean_mdd": round(statistics.mean(per[v]["mdd"] for v in names), 1),
                              "mean_wr": round(statistics.mean(per[v]["wr"] for v in names), 1), "avgs": avgs}
                set_mode("cur")
                out["_paired_cur_gt_def"] = round(sum(c > d for c, d in zip(out["cur"]["avgs"], out["def"]["avgs"])) / len(names), 2)
                res[sid][w] = out
                print(sid, w, {m: {k: out[m][k] for k in ("mean_avg", "median_avg", "mean_pf", "mean_mdd", "mean_wr")} for m in modes}, "paired", out["_paired_cur_gt_def"], flush=True)
                Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1))
if __name__ == "__main__":
    main()
