"""分策略"排除某 skill"的生产同构对照（L-071，只读、零网络、零DB写）。

动机：winrate_per_strategy_parallel 是 path-A solo（非生产同构，small-cap 冻结0出票即证偏差），
只能探索。本脚本用 ProductionRuleReplayCandidateBuilder（走同一个 ProductionRecommendationAssembly.
aggregate）构建两次快照：A=全部启用技能；B=排除目标技能。再用 evaluate_selection_policy +
_simulation 因果出场比较两臂选中票逐笔/日cohort，定位"剔除该策略"对生产选股的真实影响。

用法:
  python scripts/research_strategy_exclude_canonical.py --start 2025-06-02 --end 2025-06-30 \
      --exclude weak-market-strong-stock-reversal
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path
import pandas as pd
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.engine.backtest import ExecutionPolicy
from app.engine.skill_loader import load_skills
from app.engine.production_policy import (
    load_production_selection_policy, evaluate_selection_policy, SelectionPolicyContext)
from app.services.backtest.candidate_snapshot_builder import ProductionRuleReplayCandidateBuilder
from app.services.backtest.policy_experiment import (
    ScoredCandidateSnapshot, _thaw_json, _simulate_card)
from app.services.backtest.stock_metadata import StockMetadataHistoryProvider
from app.models.tables import SkillRegistry
from sqlalchemy import select


def enabled_skill_ids(db) -> list[str]:
    reg = {k: bool(v) for k, v in db.execute(select(SkillRegistry.skill_id, SkillRegistry.enabled)).all()}
    out = []
    for s in load_skills(get_settings().skills_dir):
        if not (s.enabled and s.executable and s.contract):
            continue
        if reg and not reg.get(s.skill_id, False):
            continue
        out.append(s.skill_id)
    return sorted(out)


def build(db, start, end, skill_ids, ep, score_line, output_count):
    snap = ProductionRuleReplayCandidateBuilder(
        db, stock_metadata_provider=StockMetadataHistoryProvider(db), historical_replay=True
    ).build(start_date=start, end_date=end, execution_policy=ep,
            score_line=score_line, output_count=output_count, skill_ids=skill_ids)
    # 经 canonical 往返，保证与 holdout 处理完全一致
    return ScoredCandidateSnapshot.from_payload(snap.canonical_payload())


def arm_trades(snap, pol, ep):
    rows = []
    for day in snap.days:
        cards = _thaw_json(day.cards)
        rec = evaluate_selection_policy(cards, SelectionPolicyContext(72., 10, day.market_snapshot), pol).recommendations
        cm = {str(c.get("code")): c for c in cards}
        for c in rec:
            tr = (_simulate_card(day, cm[str(c.get("code"))], ep) or {}).get("trade")
            if tr and tr.executed:
                rows.append({"dt": day.signal_date, "code": str(c.get("code")), "net": float(tr.net_return_pct)})
    return pd.DataFrame(rows)


def summarize(tag, df):
    if df.empty:
        print(f"  [{tag}] 无成交"); return
    net = df.net
    wr = (net > 0).mean(); pf = net[net > 0].sum() / max(abs(net[net < 0].sum()), 1e-9)
    daily = df.groupby("dt").net.mean()
    print(f"  [{tag}] n={len(df)} 逐笔均{net.mean():+.4f} 胜率{wr:.3f} PF{pf:.3f} 日cohort均{daily.mean():+.4f} 出票日{df.dt.nunique()}")


def compare(ta, tb, tag):
    summarize("A全部", ta); summarize(f"B排除{tag}", tb)
    ka = set(zip(ta.dt, ta.code)); kb = set(zip(tb.dt, tb.code))
    only_a = ta[[(d, c) in ka - kb for d, c in zip(ta.dt, ta.code)]]
    only_b = tb[[(d, c) in kb - ka for d, c in zip(tb.dt, tb.code)]]
    print(f"  >> {tag}: A有B无 {len(only_a)} 张 均净 {only_a.net.mean() if len(only_a) else float('nan'):+.4f}; "
          f"递补 {len(only_b)} 张 均净 {only_b.net.mean() if len(only_b) else float('nan'):+.4f}")
    # 日 cohort 配对差
    da = ta.groupby("dt").net.mean(); db_ = tb.groupby("dt").net.mean()
    j = da.to_frame("a").join(db_.to_frame("b"), how="outer").fillna(0.0)
    dlt = j.b - j.a
    print(f"     日cohort Δ均{dlt.mean():+.4f} 变正天{(dlt>0).sum()}/变负天{(dlt<0).sum()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True); ap.add_argument("--end", required=True)
    ap.add_argument("--exclude", action="append", default=[])
    ap.add_argument("--score-line", type=float, default=72.0); ap.add_argument("--output-count", type=int, default=10)
    args = ap.parse_args()
    ep = ExecutionPolicy(); pol = load_production_selection_policy()  # 当前生产默认版本（v4），不硬编码
    db = SessionLocal()
    try:
        all_ids = enabled_skill_ids(db)
        print(f"全部启用 {len(all_ids)}: {all_ids}", flush=True)
        print(f"构建 A=全部 窗口{args.start}~{args.end} ...", flush=True)
        sa = build(db, args.start, args.end, None, ep, args.score_line, args.output_count)
        ta = arm_trades(sa, pol, ep); del sa
        for ex in args.exclude:
            keep = [s for s in all_ids if s != ex]
            print(f"构建 B=排除 {ex} ...", flush=True)
            sb = build(db, args.start, args.end, keep, ep, args.score_line, args.output_count)
            tb = arm_trades(sb, pol, ep); del sb
            compare(ta, tb, ex)
    finally:
        db.close()


if __name__ == "__main__":
    main()
