"""生产同构双窗口政策对照器（L-071 canonical harness，只读）。

目标：政策级结论只信与生产同构的选择/执行口径，不用手写 cohort 反推生产阈值。

- 冻结窗 holdout_244d：ScoredCandidateSnapshot + ProductionPolicyBacktestRunner._run_arm
  （内部调用真实 evaluate_selection_policy→apply_pool_rules + simulation.simulate，
  sellable=tradable），完全生产同构。
- 前瞻窗 2026-02-09~08-05（canonical run btr_fa625，score>=70）：DB 已对每个候选用同一
  因果引擎算好净收益；本脚本用真实 evaluate_selection_policy（市场级 regime×情绪 override、
  去重、行业分散、daily_output_limit、高潮 display divisor 全走生产代码）重放"选哪些"，
  再 join canonical 净收益。前瞻精简卡缺 chg20/support_ma5/旧regime串，单票惩罚无法复现，
  但 v1/v2 差异只来自 override 组合（v2 override 优先级最高直接定线；缺单票字段只会让 v1
  侧偏宽松，结论偏保守）。

度量（按简报口径）：逐笔等权 avg_net、胜率、盈亏比 PF、n；日度 cohort 等权（空日记0）的
均值/累计/MDD/盈天占比/出票天；配对日差 bootstrap 95%CI；被 v2 拦掉交易的机会成本符号；
top4/6/8/10 敏感性。不用 compute_metrics.total_net_return_pct。
"""
from __future__ import annotations
import json, sys, dataclasses
from pathlib import Path
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.backtest import ExecutionPolicy
from app.engine.production_policy import (
    load_production_selection_policy, evaluate_selection_policy, SelectionPolicyContext)
from app.engine.aggregator import _market_base, _market_sentiment
from app.services.backtest.policy_experiment import (
    ProductionPolicyBacktestRunner, ScoredCandidateSnapshot, _purged_split,
    CanonicalPolicyExperimentSpec, _thaw_json)
from scripts.research_forward_validation import build_market_snapshots

OUT = B / "output" / "research"
RUN = "btr_fa625f3c41c147edbee3edb7d3adede5"
FWD_CACHE = OUT / "forward_canonical.pkl"
SLEEVE = 1.0 / 6.0  # 与 _metric_summary 的 DAILY_SLEEVE_FRACTION 对齐，仅用于累计/MDD 可比


def _db_url():
    for line in (B / ".env").read_text().splitlines():
        if line.startswith("ASHARE_DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("no db url")


# ---------------- 前瞻：真实选择重放 + canonical 净收益 ----------------
def build_forward_canonical():
    if FWD_CACHE.exists():
        return pd.read_pickle(FWD_CACHE)
    eng = create_engine(_db_url())
    q = text("""SELECT a.signal_date dt, a.code,
                      JSON_EXTRACT(a.signal_json,'$.score') score,
                      JSON_UNQUOTE(JSON_EXTRACT(a.signal_json,'$.industry')) industry,
                      JSON_EXTRACT(t.trade_json,'$.net_return_pct') net
               FROM backtest_candidates a JOIN backtest_trades t
               ON a.backtest_id=t.backtest_id AND a.signal_date=t.signal_date AND a.code=t.code
               WHERE a.backtest_id=:r""")
    with eng.connect() as c:
        df = pd.DataFrame(c.execute(q, {"r": RUN}).mappings().all())
    df["score"] = df.score.astype(float); df["net"] = df.net.astype(float)
    df["industry"] = df.industry.fillna("未知")
    df.to_pickle(FWD_CACHE)
    return df


def forward_selected(fw: pd.DataFrame, snaps: dict, policy, top_n: int | None = None) -> pd.DataFrame:
    """对每个前瞻日跑真实 evaluate_selection_policy，返回被选交易（含净收益）。"""
    rows = []
    for d, gp in fw.groupby("dt"):
        snap = snaps.get(d)
        cards = [{"code": str(r.code), "final_score": float(r.score),
                  "industry": str(r.industry), "signal_price": 10.0}
                 for r in gp.itertuples()]
        res = evaluate_selection_policy(
            cards,
            SelectionPolicyContext(score_line=72.0, output_count=10, market_snapshot=dict(snap or {})),
            policy)
        sel = list(res.recommendations)
        if top_n is not None:
            sel = sel[:top_n]
        netmap = {(str(r.code)): float(r.net) for r in gp.itertuples()}
        b = _market_base(snap); se = _market_sentiment(snap)
        for c in sel:
            code = str(c.get("code"))
            if code in netmap:
                rows.append({"dt": d, "code": code, "net": netmap[code],
                             "base": b, "sent": se, "key": f"{b}·{se}",
                             "score": float(c.get("final_score") or 0)})
    return pd.DataFrame(rows)


# ---------------- 冻结：_run_arm 完全生产同构 ----------------
_FROZEN = {}
def frozen_selected(policy) -> pd.DataFrame:
    if not _FROZEN:
        v1 = load_production_selection_policy(version=1)  # noqa
        snap = ScoredCandidateSnapshot.from_payload(
            json.loads((B / "output/frozen_snapshots/holdout_244d.json").read_text()))
        ep = ExecutionPolicy()
        split = _purged_split(tuple(d.signal_date for d in snap.days),
                              purge_days=ep.entry_lookahead_days + ep.horizon_days)
        _FROZEN["snap"] = snap; _FROZEN["ep"] = ep; _FROZEN["split"] = split
        _FROZEN["mbse"] = {d.signal_date: (_market_base(d.market_snapshot),
                                          _market_sentiment(d.market_snapshot))
                           for d in snap.days}
    snap, ep, split, mbse = _FROZEN["snap"], _FROZEN["ep"], _FROZEN["split"], _FROZEN["mbse"]
    runner = ProductionPolicyBacktestRunner(repository=None)
    spec = CanonicalPolicyExperimentSpec.__new__(CanonicalPolicyExperimentSpec)
    for k, val in [("candidate_snapshot", snap), ("score_line", 72.),
                   ("output_count", 10), ("execution_policy", ep)]:
        object.__setattr__(spec, k, val)
    _, rec = runner._run_arm(spec, policy, split)
    rows = []
    for key in rec["selected_keys"]:
        dt, code = key.split(":", 1)
        tr = (rec["outcomes"].get(key) or {}).get("trade")
        if tr is None or not tr.executed:
            continue
        mb, se = mbse[dt]
        # 取该选中卡 final_score（从 snapshot 当日卡片里找）
        score = None
        dayobj = next((d for d in snap.days if d.signal_date == dt), None)
        if dayobj is not None:
            for c in _thaw_json(dayobj.cards):
                if str(c.get("code")) == code:
                    score = float(c.get("final_score") or 0); break
        rows.append({"dt": dt, "code": code, "net": float(tr.net_return_pct),
                     "base": mb, "sent": se, "key": f"{mb}·{se}", "score": score,
                     "split": next((w for w, ds in split.items() if dt in set(ds)), "?")})
    return pd.DataFrame(rows)


# ---------------- 度量 ----------------
def _bootstrap_ci(delta: np.ndarray, confidence=0.95, draws=4000, seed=7):
    delta = np.asarray(delta, float)
    if len(delta) < 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(delta), size=(draws, len(delta)))
    means = delta[idx].mean(axis=1)
    a = (1 - confidence) / 2
    return float(np.quantile(means, a)), float(np.quantile(means, 1 - a))


def trade_metrics(df: pd.DataFrame) -> dict:
    if not len(df):
        return {"n": 0, "avg_net": 0.0, "win": 0.0, "pf": None}
    win = df.net[df.net > 0].sum(); loss = abs(df.net[df.net < 0].sum())
    return {"n": int(len(df)), "avg_net": round(df.net.mean(), 4),
            "win": round((df.net > 0).mean(), 4),
            "pf": round(win / loss, 3) if loss else None}


def daily_cohort(df: pd.DataFrame, all_dates) -> pd.Series:
    g = df.groupby("dt").net.mean()
    return g.reindex(sorted(all_dates)).fillna(0.0)


def cohort_metrics(co: pd.Series) -> dict:
    eq = (1 + co * SLEEVE / 100).cumprod()
    mdd = float(((eq / eq.cummax() - 1) * 100).min())
    return {"days": int(len(co)), "tickets": int((co != 0).sum()),
            "daily_avg": round(co.mean(), 4),
            "cum": round((eq.iloc[-1] - 1) * 100, 2),
            "mdd": round(mdd, 2), "pos_day": round((co > 0).mean(), 4)}


def compare(name_window: str, sel_a: pd.DataFrame, sel_b: pd.DataFrame, all_dates):
    ta, tb = trade_metrics(sel_a), trade_metrics(sel_b)
    ca, cb = daily_cohort(sel_a, all_dates), daily_cohort(sel_b, all_dates)
    ma, mb = cohort_metrics(ca), cohort_metrics(cb)
    delta = (cb - ca)
    changed = delta[delta != 0]
    lo, hi = _bootstrap_ci(changed.values)
    monthly = (cb - ca).groupby(lambda d: d[:7]).sum()
    return {"window": name_window, "v1_trade": ta, "v2_trade": tb,
            "v1_cohort": ma, "v2_cohort": mb,
            "paired_changed_days": int(len(changed)),
            "paired_delta_daily_avg": round(changed.mean(), 4) if len(changed) else 0.0,
            "paired_ci95": [round(lo, 4), round(hi, 4)],
            "better_day_share": round((changed > 0).mean(), 3) if len(changed) else None,
            "monthly_pos_share": round((monthly > 0).mean(), 3) if len(monthly) else None,
            "_ca": ca, "_cb": cb}


def opportunity_cost(sel_v1: pd.DataFrame, sel_v2: pd.DataFrame) -> dict:
    """v1 选、v2 拦掉的交易：其净收益分布（好的收紧应让被拦票均值为负）。"""
    a = set(zip(sel_v1.dt, sel_v1.code)); bset = set(zip(sel_v2.dt, sel_v2.code))
    dropped = sel_v1[[(t in a - bset) for t in zip(sel_v1.dt, sel_v1.code)]]
    if not len(dropped):
        return {"n": 0}
    by = {}
    for k, g in dropped.groupby("key"):
        by[k] = {"n": int(len(g)), "avg_net": round(g.net.mean(), 4), "win": round((g.net > 0).mean(), 3)}
    return {"n": int(len(dropped)), "avg_net": round(dropped.net.mean(), 4),
            "win": round((dropped.net > 0).mean(), 3), "by_cell": dict(sorted(by.items()))}


def run_all():
    v1 = load_production_selection_policy(version=1)
    v2 = load_production_selection_policy(version=2)
    ev = {}
    # ---- frozen ----
    fz1, fz2 = frozen_selected(v1), frozen_selected(v2)
    fz_dates = sorted(set(fz1.dt) | set(fz2.dt))
    ev["frozen"] = {k: v for k, v in compare("frozen", fz1, fz2, fz_dates).items() if not k.startswith("_")}
    ev["frozen"]["opportunity_cost"] = opportunity_cost(fz1, fz2)
    ev["frozen"]["split_trade"] = {
        "v1": {w: trade_metrics(fz1[fz1.split == w]) for w in ("train", "validation", "out_of_sample")},
        "v2": {w: trade_metrics(fz2[fz2.split == w]) for w in ("train", "validation", "out_of_sample")}}
    # ---- forward ----
    fw = build_forward_canonical()
    dates = sorted(fw.dt.unique()); snaps = build_market_snapshots(dates)
    have = [d for d in dates if d in snaps]
    fw = fw[fw.dt.isin(have)]
    fw1 = forward_selected(fw, snaps, v1)
    fw2 = forward_selected(fw, snaps, v2)
    ev["forward"] = {k: v for k, v in compare("forward", fw1, fw2, have).items() if not k.startswith("_")}
    ev["forward"]["opportunity_cost"] = opportunity_cost(fw1, fw2)
    # top-N 敏感性（前瞻）
    sens = {}
    for n in (4, 6, 8, 10):
        a = forward_selected(fw, snaps, v1, n); b = forward_selected(fw, snaps, v2, n)
        ca, cb = daily_cohort(a, have), daily_cohort(b, have)
        d = (cb - ca); ch = d[d != 0]; lo, hi = _bootstrap_ci(ch.values)
        sens[f"top{n}"] = {"v1_cum": cohort_metrics(ca)["cum"], "v2_cum": cohort_metrics(cb)["cum"],
                           "v1_avg": trade_metrics(a)["avg_net"], "v2_avg": trade_metrics(b)["avg_net"],
                           "ci95": [round(lo, 4), round(hi, 4)]}
    ev["forward"]["topN_sensitivity"] = sens
    (OUT / "canonical_two_window_v1v2.json").write_text(json.dumps(ev, ensure_ascii=False, indent=2))
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "split_trade"}
                      for k, v in ev.items()}, ensure_ascii=False, indent=2))
    print("\nFROZEN split trade:")
    print(json.dumps(ev["frozen"]["split_trade"], ensure_ascii=False, indent=1))
    return ev


if __name__ == "__main__":
    run_all()
