"""L-081 gap 硬否决【生产同构 canonical】三窗口终审（只读）。
等价新增一条硬池规则：信号日隔夜 gap=open/prevclose-1 <= thr 的候选在选择前剔除，
随后由真实 evaluate_selection_policy / _run_arm 自然递补下一张卡（不人为留空）。
对照 baseline v2：逐笔/日cohort/配对日差 bootstrap CI/被砍票机会成本/出票天/topN。
"""
from __future__ import annotations
import json, sys, dataclasses
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import create_engine, text

B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.backtest import ExecutionPolicy
from app.engine.production_policy import load_production_selection_policy, evaluate_selection_policy, SelectionPolicyContext
from app.engine.aggregator import _market_base, _market_sentiment
from app.services.backtest.policy_experiment import (
    ProductionPolicyBacktestRunner, ScoredCandidateSnapshot, _purged_split,
    CanonicalPolicyExperimentSpec, _thaw_json)
from scripts.research_forward_validation import build_market_snapshots
from scripts.research_canonical_two_window import compare, trade_metrics, daily_cohort, cohort_metrics

OUT = B / "output" / "research"
RUN_FWD = "btr_fa625f3c41c147edbee3edb7d3adede5"
RUN3 = "btr_2bec6fed6c344eaa819e533b8220a0d6"


def _db():
    for line in (B / ".env").read_text().splitlines():
        if line.startswith("ASHARE_DATABASE_URL="):
            return create_engine(line.split("=", 1)[1].strip().strip('"').strip("'"))
    raise RuntimeError


def gap_map(codes, d0, d1):
    q = text("""SELECT code, trade_date dt, open, close FROM daily_kbars
                WHERE code IN :codes AND trade_date BETWEEN :a AND :b ORDER BY code, trade_date""")
    with _db().connect() as c:
        df = pd.DataFrame(c.execute(q, {"codes": tuple(codes), "a": d0, "b": d1}).mappings().all())
    df["dt"] = df.dt.astype(str); df["code"] = df.code.astype(str)
    df = df.sort_values(["code", "dt"])
    df["prev"] = df.groupby("code").close.shift(1)
    df["gap"] = df.open / df.prev - 1
    return {(r.dt, r.code): r.gap for r in df.itertuples() if pd.notna(r.gap)}


def run_rows(run_id):
    q = text("""SELECT a.signal_date dt, a.code,
                      JSON_EXTRACT(a.signal_json,'$.score') score,
                      JSON_UNQUOTE(JSON_EXTRACT(a.signal_json,'$.industry')) industry,
                      JSON_EXTRACT(t.trade_json,'$.net_return_pct') net
               FROM backtest_candidates a JOIN backtest_trades t
               ON a.backtest_id=t.backtest_id AND a.signal_date=t.signal_date AND a.code=t.code
               WHERE a.backtest_id=:r""")
    with _db().connect() as c:
        df = pd.DataFrame(c.execute(q, {"r": run_id}).mappings().all())
    for col in ("score", "net"): df[col] = df[col].astype(float)
    df["dt"] = df.dt.astype(str); df["code"] = df.code.astype(str)
    df["industry"] = df.industry.fillna("未知")
    return df


def select_forward(fw, snaps, policy, gmap, thr):
    rows = []
    for d, gp in fw.groupby("dt"):
        snap = snaps.get(d)
        cards = []
        for r in gp.itertuples():
            g = gmap.get((d, str(r.code)))
            if g is not None and g <= thr:  # 硬预过滤
                continue
            cards.append({"code": str(r.code), "final_score": float(r.score),
                          "industry": str(r.industry), "signal_price": 10.0})
        res = evaluate_selection_policy(
            cards, SelectionPolicyContext(score_line=72.0, output_count=10, market_snapshot=dict(snap or {})), policy)
        netmap = {str(r.code): float(r.net) for r in gp.itertuples()}
        b = _market_base(snap); se = _market_sentiment(snap)
        for c in res.recommendations:
            code = str(c.get("code"))
            if code in netmap:
                rows.append({"dt": d, "code": code, "net": netmap[code], "key": f"{b}·{se}"})
    return pd.DataFrame(rows)


def frozen_arm(snap, split, policy, gmap, thr):
    if thr is None:
        filt = snap
    else:
        days = []
        for day in snap.days:
            kept = tuple(c for c in _thaw_json(day.cards)
                         if (gmap.get((day.signal_date, str(c.get("code")))) is None
                             or gmap.get((day.signal_date, str(c.get("code")))) > thr))
            days.append(dataclasses.replace(day, cards=kept))
        filt = dataclasses.replace(snap, days=tuple(days))
    runner = ProductionPolicyBacktestRunner(repository=None)
    spec = CanonicalPolicyExperimentSpec.__new__(CanonicalPolicyExperimentSpec)
    for k, v in [("candidate_snapshot", filt), ("score_line", 72.), ("output_count", 10),
                 ("execution_policy", ExecutionPolicy())]:
        object.__setattr__(spec, k, v)
    _, rec = runner._run_arm(spec, policy, split)
    mbse = {d.signal_date: (_market_base(d.market_snapshot), _market_sentiment(d.market_snapshot))
            for d in filt.days}
    rows = []
    for key in rec["selected_keys"]:
        dt, code = key.split(":", 1)
        tr = (rec["outcomes"].get(key) or {}).get("trade")
        if tr is None or not tr.executed: continue
        mb, se = mbse[dt]
        rows.append({"dt": dt, "code": code, "net": float(tr.net_return_pct), "key": f"{mb}·{se}"})
    return pd.DataFrame(rows)


def opp(base, chal):
    a = set(zip(base.dt, base.code)); b = set(zip(chal.dt, chal.code))
    drop = base[[t in a - b for t in zip(base.dt, base.code)]]
    if not len(drop): return {}
    return {"n": len(drop), "avg": round(drop.net.mean(), 3), "win": round((drop.net > 0).mean(), 3)}


def main():
    v2 = load_production_selection_policy(version=2)
    result = {}
    # ---- frozen ----
    raw = json.loads((B / "output/frozen_snapshots/holdout_244d.json").read_text())
    snap = ScoredCandidateSnapshot.from_payload(raw)
    ep = ExecutionPolicy()
    split = _purged_split(tuple(d.signal_date for d in snap.days),
                          purge_days=ep.entry_lookahead_days + ep.horizon_days)
    codes = {str(c.get("code")) for d in snap.days for c in _thaw_json(d.cards)}
    gfz = gap_map(codes, "2024-12-01", "2026-01-31")
    fz0 = frozen_arm(snap, split, v2, gfz, None)
    # ---- forward/run3 ----
    fw = run_rows(RUN_FWD); r3 = run_rows(RUN3)
    r3 = r3[(r3.dt >= "2026-08-07") & (r3.dt <= "2026-09-02")]  # 仅真正第三窗
    gfw = gap_map(set(fw.code), "2026-01-01", "2026-08-10")
    gr3 = gap_map(set(r3.code), "2026-07-01", "2026-09-10")
    snaps_fw = build_market_snapshots(sorted(fw.dt.unique()))
    snaps_r3 = build_market_snapshots(sorted(r3.dt.unique()))
    fw0 = select_forward(fw, snaps_fw, v2, gfw, -9)  # thr=-9 不过滤=baseline
    r30 = select_forward(r3, snaps_r3, v2, gr3, -9)

    for thr in (0.0, -0.01, -0.02):
        print(f"\n################# gap 硬否决 thr={thr:+.2f} #################")
        fz1 = frozen_arm(snap, split, v2, gfz, thr)
        fw1 = select_forward(fw, snaps_fw, v2, gfw, thr)
        r31 = select_forward(r3, snaps_r3, v2, gr3, thr)
        for nm, b, c in [("FROZEN", fz0, fz1), ("FORWARD", fw0, fw1), ("RUN3", r30, r31)]:
            dates = sorted(set(b.dt) | set(c.dt))
            cmp_ = {k: v for k, v in compare(nm, b, c, dates).items() if not k.startswith("_")}
            cmp_["opportunity_cost(被砍票)"] = opp(b, c)
            cmp_["base_trade"] = trade_metrics(b); cmp_["chal_trade"] = trade_metrics(c)
            result[f"{nm}_thr{thr}"] = cmp_
            print(nm, json.dumps(cmp_, ensure_ascii=False))
    (OUT / "gap_canonical.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
