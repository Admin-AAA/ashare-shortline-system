"""信号"自身质量/置信度"轴三窗快筛（只读）——关闭 meta-quality / per-signal confidence 方向。

两类字段（持久化 schema 不对称，故分别取）：
 A 冻结卡（holdout_244d.json 解冻 + 因果 _simulate_card）：data_completeness_score、
   data_authenticity、evidence_quality_score、data_quality.missing_fields 个数 nmiss。
 B 前瞻/run3 backtest_candidates.signal_json（单策略信号）：confidence、setup_quality、
   strategy_independence、risk_flags 个数。
结果（2026-09 跑）：
 A 冻结选中 n=1010：comp/ev 仅二元、Spearman ±0.04；auth 常量；nmiss 仅 {5,6,7}，
   nmiss=7（12%）均 -0.219 vs nmiss=5 +0.388，弱单调但 rho 仅 -0.042，且 nmiss 是
   "当时哪些管道已上线"的覆盖伪影（L-078 已回填 dragon_tiger 等、历史缺失态被污染），
   signal_json 又未持久化这些字段 → 无法三窗复现，判死。
 B 前瞻/run3：confidence rho +0.007/+0.011、risk_flags +0.009/+0.007≈0 且非单调；
   strategy_independence 常量；setup_quality 仅二元 rho +0.027/+0.032 且本就喂给 final_score
   （重复计数）、冻结未持久化。整体无独立信息，补冻结也救不活。
"""
from __future__ import annotations
import sys, json
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import create_engine, text
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.services.backtest.policy_experiment import (
    ScoredCandidateSnapshot, _thaw_json, _simulate_card)
from app.engine.backtest import ExecutionPolicy
from app.engine.production_policy import (
    load_production_selection_policy, evaluate_selection_policy, SelectionPolicyContext)
RUNS = [("前瞻", "btr_fa625f3c41c147edbee3edb7d3adede5"),
        ("run3", "btr_2bec6fed6c344eaa819e533b8220a0d6")]


def grad(df, col):
    x = df[[col, "net"]].dropna()
    if len(x) < 50 or x[col].nunique() < 2:
        print(f"  {col}: 不足/常量 nunique={x[col].nunique()}"); return
    try:
        x["q"] = pd.qcut(x[col], 5, labels=False, duplicates="drop")
    except Exception:
        return
    if x.q.nunique() < 2:
        print(f"  {col}: 单档"); return
    g = x.groupby("q").net.mean(); rho = x[[col, "net"]].corr(method="spearman").iloc[0, 1]
    print(f"  {col}: rho={rho:+.4f} 分位 " + " ".join(f"{v:+.3f}" for v in g.values))


def frozen_quality():
    snap = ScoredCandidateSnapshot.from_payload(
        json.loads((B / "output/frozen_snapshots/holdout_244d.json").read_text()))
    pol = load_production_selection_policy(version=2); ep = ExecutionPolicy(); rows = []
    for day in snap.days:
        cards = _thaw_json(day.cards); cm = {str(c.get("code")): c for c in cards}
        rec = evaluate_selection_policy(cards, SelectionPolicyContext(72., 10, day.market_snapshot), pol).recommendations
        for c in rec:
            tr = (_simulate_card(day, cm[str(c.get("code"))], ep) or {}).get("trade")
            if not (tr and tr.executed):
                continue
            dq = c.get("data_quality") or {}; mf = dq.get("missing_fields") or []
            rows.append(dict(net=float(tr.net_return_pct),
                             comp=float(c.get("data_completeness_score") or np.nan),
                             auth=float(c.get("data_authenticity") or np.nan),
                             ev=float(c.get("evidence_quality_score") or np.nan),
                             nmiss=len(mf)))
    df = pd.DataFrame(rows); print(f"[A] 冻结选中 n={len(df)} nmiss分布={df.nmiss.value_counts().sort_index().to_dict()}")
    for col in ["comp", "auth", "ev", "nmiss"]:
        grad(df, col)


def signal_fields():
    url = [l.split("=", 1)[1].strip().strip('"').strip("'") for l in (B / ".env").read_text().splitlines()
           if l.startswith("ASHARE_DATABASE_URL=")][0]
    e = create_engine(url)
    q = text("""SELECT JSON_EXTRACT(a.signal_json,'$.score') score,
        JSON_EXTRACT(a.signal_json,'$.confidence') conf,
        JSON_EXTRACT(a.signal_json,'$.setup_quality') sq,
        JSON_EXTRACT(a.signal_json,'$.strategy_independence') ind,
        JSON_LENGTH(JSON_EXTRACT(a.signal_json,'$.risk_flags')) nrisk,
        JSON_EXTRACT(t.trade_json,'$.net_return_pct') net
        FROM backtest_candidates a JOIN backtest_trades t
        ON a.backtest_id=t.backtest_id AND a.signal_date=t.signal_date AND a.code=t.code
        WHERE a.backtest_id=:r""")
    for nm, run in RUNS:
        with e.connect() as c:
            df = pd.DataFrame(c.execute(q, {"r": run}).mappings().all())
        for cc in ["score", "conf", "sq", "ind", "nrisk", "net"]:
            df[cc] = pd.to_numeric(df[cc], errors="coerce")
        sel = df[df.score >= 70]
        print(f"[B] {nm} 候选n={len(sel)}")
        for col in ["conf", "sq", "ind", "nrisk"]:
            grad(sel, col)


if __name__ == "__main__":
    frozen_quality(); signal_fields()
