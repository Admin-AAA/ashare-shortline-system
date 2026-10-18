"""Sprint4 §84：短线策略的慢速趋势闸门（全市场等权指数趋势）是否在 W0/W00 等弱市阶段止损、且不伤 12m/EXT。
预注册 3 个变体（信号日收盘可得，不调参）：
  ma20   : 等权指数收盘 < MA20 → 当日不出票
  ma60   : 等权指数收盘 < MA60 → 当日不出票
  ma2060 : 等权指数 MA20 < MA60 → 当日不出票
每个策略先套线上日闸门（与 strategy.yaml 一致的近似），再按当前 exit_overrides/MA 破位出场逐票重模拟。
采纳标准（预设）：4 窗（12m/EXT/W0/W00）中 ≥3 窗每笔均值提高，12m 每笔均值与 PF 均不降，12m 笔数保留 ≥50%。
"""
from __future__ import annotations
import argparse, json, os, sys
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
from scripts.research_stability_gates import panels, ur
from scripts.research_exit_resim import fetch_bars, variant, Day

IDX = Path("/tmp/eqw_idx.json")

def build_index():
    if IDX.exists(): return json.loads(IDX.read_text())
    sys.path.insert(0, str(B / "scripts" / "midterm"))
    import polars as pl
    from lt_data import prices
    df = prices().filter(pl.col("trade_date") >= "2021-06-01").filter(pl.col("amount") > 0)
    d = df.group_by("trade_date").agg(pl.col("pct_chg").clip(-20, 20).mean().alias("r"), pl.len().alias("n")).sort("trade_date")
    d = d.filter(pl.col("n") >= 1500)
    lvl = 1.0; out = {}
    rows = d.to_dicts(); closes = []
    for r in rows:
        lvl *= 1 + r["r"] / 100; closes.append(lvl)
        i = len(closes)
        ma20 = sum(closes[-20:]) / 20 if i >= 20 else None
        ma60 = sum(closes[-60:]) / 60 if i >= 60 else None
        out[r["trade_date"]] = {"c": lvl, "ma20": ma20, "ma60": ma60, "n": r["n"]}
    IDX.write_text(json.dumps(out)); return out

LU = lambda ms: float(ms.get("limit_up_count") or 0)
LD = lambda ms: float(ms.get("limit_down_count") or 0)
LIVE_DAY = {  # 线上 strategy.yaml 日闸门的面板近似
    "chen-xiaoqun-skill": lambda ms: 0.35 <= ur(ms) < 0.50,
    "chao-gu-yang-jia-skill": lambda ms: 0.35 <= ur(ms) < 0.50 or ur(ms) > 0.75 or ur(ms) < 0.30,
    "sector-relative-strength-leader": lambda ms: LU(ms) - LD(ms) < 40,
    "oversold-capital-reversal": lambda ms: False,
    "high-range-breakout-momentum": lambda ms: False,
}
CARD = {"oversold-capital-reversal": lambda c: not (c.get("feature_snapshot") or {}).get("is_limit_up")}

def trend_skip(idx, d, v):
    x = idx.get(d)
    if not x or x["ma60"] is None: return False
    if v == "ma20": return x["c"] < x["ma20"]
    if v == "ma60": return x["c"] < x["ma60"]
    if v == "ma2060": return x["ma20"] < x["ma60"]
    return False

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--sids", default=",".join(LIVE_DAY)); ap.add_argument("--out", default="output/research/sprint4/trend_gate.json")
    a = ap.parse_args(); idx = build_index()
    base_pol = load_production_selection_policy(); res = {}
    ep = variant(); lim = ep.entry_lookahead_days + ep.horizon_days + 2
    with SessionLocal() as db:
        for sid in a.sids.split(","):
            pol = standalone_selection_policy(base_pol, sid, 3); res[sid] = {}
            for w, f in panels(sid).items():
                p = json.loads(f.read_text()); _register_only(p)
                days = [d["signal_date"] for d in p["days"]]
                cov = sum(1 for d in days if d in idx and idx[d]["ma60"] is not None)
                cards = {(d["signal_date"], c["code"]): c for d in p["days"] for c in d["cards"]}
                base = RULES[f"only:{sid}"]; out = {"_idx_cov": f"{cov}/{len(days)}"}
                for v in ("base", "ma20", "ma60", "ma2060"):
                    def rule(cs, d, _v=v):
                        if LIVE_DAY[sid](d["market_snapshot"]): return []
                        if _v != "base" and trend_skip(idx, d["signal_date"], _v): return []
                        cs = base(cs, d)
                        if sid in CARD: cs = [c for c in cs if CARD[sid](c)]
                        return cs
                    _, rows = run_rule(rule, p, pol, {"output_count": 3, "score_line": 72.0}, apply_global_weights=False)
                    by_day = defaultdict(set)
                    for r in rows: by_day[r["dt"]].add(r["code"])
                    bars = fetch_bars(db, by_day); nrows = []
                    for r in rows:
                        day = Day(); day.signal_date = r["dt"]; day.outcome_bars = {r["code"]: tuple(bars[r["dt"]].get(r["code"], ())[:lim])}
                        tr = (_simulate_card(day, cards[(r["dt"], r["code"])], ep) or {}).get("trade")
                        if tr and tr.executed: nrows.append({"dt": r["dt"], "code": r["code"], "net": float(tr.net_return_pct)})
                    m = _metrics(nrows, days)
                    out[v] = {"n": m["n"], "wr": round(m["wr"] * 100, 1), "avg": round(m["avg"], 2), "pf": round(m["pf"], 2), "mdd": round(m["mdd"], 1), "sum": round(m["sum"], 1)} if m else {"n": 0}
                res[sid][w] = out
                print(sid, w, json.dumps(out, ensure_ascii=False), flush=True)
                Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1))
if __name__ == "__main__":
    main()
