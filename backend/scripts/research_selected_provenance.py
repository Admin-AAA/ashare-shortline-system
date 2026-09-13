"""选中卡"策略溯源/共识结构"三窗导出（全管线生产同构重放，只读）。

背景：L-084(c) 判"单卡共识数/逐策略标签因持久化快照折叠而不可三窗"。本脚本用
ProductionRuleReplayCandidateBuilder 全管线重放（当前 _pre_policy_card 已保留每个 signal 的
strategy_id/phase/score/hard_veto），对每张【生产选中卡】导出背书策略结构 + 因果净收益，
使"主导策略/共识数/信号离散度"这类评分内部量首次可三窗检验。仅导出、不下结论。

用法:
  python scripts/research_selected_provenance.py --start 2025-01-15 --end 2026-01-16 --tag frozen
"""
from __future__ import annotations
import argparse, json, sys, statistics
from pathlib import Path
import pandas as pd
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.db.session import SessionLocal
from app.engine.backtest import ExecutionPolicy
from app.engine.production_policy import (
    load_production_selection_policy, evaluate_selection_policy, SelectionPolicyContext)
from app.services.backtest.candidate_snapshot_builder import ProductionRuleReplayCandidateBuilder
from app.services.backtest.policy_experiment import _thaw_json, _simulate_card
from app.services.backtest.stock_metadata import StockMetadataHistoryProvider


def card_provenance(card: dict) -> dict:
    sigs = [s for s in card.get("strategy_signals", []) if isinstance(s, dict) and not s.get("hard_veto")]
    scores = [float(s.get("score", 0)) for s in sigs]
    ids = [str(s.get("strategy_id", "?")) for s in sigs]
    dom = ids[scores.index(max(scores))] if scores else "none"
    return {
        "n_sig": len(sigs),
        "dominant": dom,
        "ids": "|".join(sorted(ids)),
        "sig_mean": round(statistics.fmean(scores), 2) if scores else 0.0,
        "sig_max": round(max(scores), 2) if scores else 0.0,
        "sig_std": round(statistics.pstdev(scores), 2) if len(scores) >= 2 else 0.0,
        "sig_range": round(max(scores) - min(scores), 2) if len(scores) >= 2 else 0.0,
        "high_agree": int(sum(1 for x in scores if x >= 70) >= 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True); ap.add_argument("--end", required=True)
    ap.add_argument("--tag", required=True); ap.add_argument("--score-line", type=float, default=72.0)
    ap.add_argument("--output-count", type=int, default=10)
    args = ap.parse_args()
    ep = ExecutionPolicy(); pol = load_production_selection_policy()  # 当前生产默认版本（v4）
    db = SessionLocal()
    try:
        snap = ProductionRuleReplayCandidateBuilder(
            db, stock_metadata_provider=StockMetadataHistoryProvider(db), historical_replay=True
        ).build(start_date=args.start, end_date=args.end, execution_policy=ep,
                score_line=args.score_line, output_count=args.output_count, skill_ids=None)
    finally:
        db.close()
    rows = []
    for day in snap.days:
        cards = _thaw_json(day.cards)
        rec = evaluate_selection_policy(cards, SelectionPolicyContext(72., 10, day.market_snapshot), pol).recommendations
        cm = {str(c.get("code")): c for c in cards}
        for c in rec:
            code = str(c.get("code")); card = cm[code]
            tr = (_simulate_card(day, card, ep) or {}).get("trade")
            if not (tr and tr.executed):
                continue
            row = {"dt": day.signal_date, "code": code,
                   "final_score": float(card.get("final_score", card.get("score", 0))),
                   "net": float(tr.net_return_pct), "exit": getattr(tr, "exit_reason", "")}
            row.update(card_provenance(card))
            rows.append(row)
    df = pd.DataFrame(rows)
    out = B / "output/research/provenance"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / f"{args.tag}.csv", index=False)
    print(f"[{args.tag}] n={len(df)} 出票日{df.dt.nunique()} 逐笔均{df.net.mean():+.4f}")
    print("== 按主导策略 ==")
    g = df.groupby("dominant").agg(n=("net", "size"), avg=("net", "mean"), wr=("net", lambda x: (x > 0).mean()))
    print(g.round(3).sort_values("n", ascending=False).to_string())
    print("== 按背书策略数 n_sig ==")
    g2 = df.groupby("n_sig").agg(n=("net", "size"), avg=("net", "mean"), wr=("net", lambda x: (x > 0).mean()))
    print(g2.round(3).to_string())


if __name__ == "__main__":
    main()
