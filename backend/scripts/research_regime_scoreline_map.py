"""regime×情绪×分数线 因果机会图谱 + 出票率策略对照（只读）。

L-065 指定的合法出路③（regime 分工 + 出票率），也是全教训库唯一有 OOS 正证据的杠杆。
生产 artifact 已含 4 个 L-060 放行覆盖；本脚本独立用生产因果出场引擎重放冻结快照，做：
1. 16 个 regime×情绪格子 × final_score 分桶的因果均净/胜率/样本（训练/OOS 分列），
   定位「正期望但被现行线压在下面的票」与「负期望却被放行的票」；
2. 三套分数线策略在【日度生产式 cohort（达线→按 final_score 取 top6 等权，无票日=现金）】
   上的组合级对照（L-015）：P0 现行 artifact / P72 统一72 / P* 数据推导的每格线；
3. walk-forward：只用训练段定每格线，锁定后在 OOS 段评估，避免用 OOS 调参。

只读，不写生产。
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.research_intraday_portfolio_levers import (  # type: ignore
    _load_snapshot, _simulate_card, TOP_N, TRAIN_FRACTION, _metric_summary)
from app.engine.aggregator import resolve_score_line_context, _market_base, _market_sentiment  # noqa
from app.engine.production_policy import (  # noqa
    DEFAULT_PRODUCTION_SELECTION_POLICY, ProductionSelectionPolicy)

OUT = Path(__file__).resolve().parents[1] / "output" / "research"
TRADES_CACHE = OUT / "regime_scoreline_trades.pkl"
BASE_LINE = 72.0
SCORE_BUCKETS = [(0,60),(60,64),(64,68),(68,72),(72,76),(76,80),(80,200)]


def build_trades() -> tuple[pd.DataFrame, dict]:
    if TRADES_CACHE.exists():
        return pd.read_pickle(TRADES_CACHE)
    days = _load_snapshot()
    rows, day_ctx = [], {}
    for day in days:
        date = str(day["signal_date"]); snap = day.get("market_snapshot") or {}
        cur = resolve_score_line_context(BASE_LINE, snap, "balanced", DEFAULT_PRODUCTION_SELECTION_POLICY)
        day_ctx[date] = {"eff_line_current": cur["effective_score_line"],
                         "market_base": cur["market_base"], "sentiment": cur["market_sentiment"]}
        for card in day.get("cards") or []:
            t = _simulate_card(day, card)
            if t is None:
                continue
            rows.append({"signal_date": date, "code": t.code, "final_score": t.final_score,
                         "net": t.net_return_pct, "market_base": t.market_base,
                         "sentiment": t.sentiment, "industry": t.industry})
    tr = pd.DataFrame(rows)
    tr.to_pickle(TRADES_CACHE)
    import pickle
    (OUT / "regime_scoreline_dayctx.pkl").write_bytes(pickle.dumps(day_ctx))
    return tr


def combo_score_map(tr: pd.DataFrame, split: str):
    print("\n===== 16 格 × final_score 分桶 因果均净收益%（胜率%/n） 训练|OOS =====")
    order_b = ["强牛", "偏多", "震荡", "弱势"]; order_s = ["高潮", "活跃", "平淡", "冰点"]
    report = {}
    for b in order_b:
        for s in order_s:
            sub = tr[(tr.market_base == b) & (tr.sentiment == s)]
            if len(sub) < 10:
                continue
            line = f"  {b}·{s:2s} "
            cells = {}
            for lo, hi in SCORE_BUCKETS:
                seg = sub[(sub.final_score >= lo) & (sub.final_score < hi)]
                trseg = seg[seg.signal_date <= split]; ooseg = seg[seg.signal_date > split]
                def cell(x):
                    if len(x) == 0: return "  ·  "
                    return f"{x.net.mean():+5.2f}/{int((x.net>0).mean()*100):02d}/n{len(x):<4d}"
                line += f"[{lo}-{hi if hi<200 else '+'}]{cell(trseg)}|{cell(ooseg)} "
                cells[f"{lo}-{hi}"] = {"train_n": len(trseg), "train_mean": round(trseg.net.mean(),3) if len(trseg) else None,
                                       "train_wr": round((trseg.net>0).mean(),3) if len(trseg) else None,
                                       "oos_n": len(ooseg), "oos_mean": round(ooseg.net.mean(),3) if len(ooseg) else None,
                                       "oos_wr": round((ooseg.net>0).mean(),3) if len(ooseg) else None}
            report[f"{b}·{s}"] = cells
            print(line)
    return report


def daily_baskets(tr: pd.DataFrame, line_map: dict | None, policy=None) -> pd.DataFrame:
    """line_map: {(base·sentiment): 固定线}；policy: 用生产 resolve 逐日线。返回日度 basket。"""
    import pickle
    day_ctx = pickle.loads((OUT / "regime_scoreline_dayctx.pkl").read_bytes())
    rows = []
    for date, gp in tr.groupby("signal_date"):
        ctx = day_ctx.get(date, {})
        key = f"{ctx.get('market_base')}·{ctx.get('sentiment')}"
        if line_map is not None:
            eff = line_map.get(key, BASE_LINE)
        else:
            eff = ctx["eff_line_current"]
        elig = gp[gp.final_score >= eff].sort_values("final_score", ascending=False)
        top = elig.head(TOP_N)
        basket = top.net.mean() if len(top) else 0.0  # 无票日 = 现金 0
        rows.append({"signal_date": date, "basket": basket, "n": len(top), "key": key,
                     "eff": eff})
    return pd.DataFrame(rows).sort_values("signal_date")


def derive_lines(tr: pd.DataFrame, split: str, min_n_bucket: int = 8):
    """训练段：对每格找『最低且其以上累计票均净>0、胜率>=50%』的线（放宽出票但保正期望）。"""
    train = tr[tr.signal_date <= split]
    lines = {}
    rationale = {}
    for (b, s), sub in train.groupby(["market_base", "sentiment"]):
        if len(sub) < min_n_bucket:
            continue
        best_line = None
        for lo, _ in SCORE_BUCKETS:
            above = sub[sub.final_score >= lo]
            if len(above) >= min_n_bucket and above.net.mean() > 0 and (above.net > 0).mean() >= 0.50:
                best_line = lo  # 从低到高，取满足正期望的最低线=最大出票
                break
        if best_line is not None:
            lines[f"{b}·{s}"] = float(best_line)
            rationale[f"{b}·{s}"] = {"line": best_line,
                                     "train_mean": round(sub[sub.final_score>=best_line].net.mean(),3),
                                     "train_wr": round((sub[sub.final_score>=best_line].net>0).mean(),3),
                                     "n": int(len(sub[sub.final_score>=best_line]))}
    return lines, rationale


def compare_policies(tr, split):
    p0 = daily_baskets(tr, None)  # 现行 artifact
    p72 = daily_baskets(tr, {})    # 空 map→默认72（每格都72）
    derived, rationale = derive_lines(tr, split)
    pstar = daily_baskets(tr, derived)
    print("\n推导每格线（训练段，正期望最低线）:", json.dumps(derived, ensure_ascii=False))
    out = {"derived_lines": rationale, "policies": {}}
    for name, df in [("P0_current", p0), ("P72_uniform", p72), ("Pstar_derived", pstar)]:
        rows = {}
        for window, dd in [("all", df), ("train", df[df.signal_date<=split]), ("oos", df[df.signal_date>split])]:
            m = _metric_summary(dd.basket)
            m["ticket_days"] = int((dd.n>0).sum()); m["zero_days"] = int((dd.n==0).sum())
            m["zero_day_pct"] = round((dd.n==0).mean()*100,1)
            m["avg_tickets_on_ticket_days"] = round(dd[dd.n>0].n.mean(),2) if (dd.n>0).any() else 0
            rows[window] = m
        out["policies"][name] = rows
    for name in ["P0_current","P72_uniform","Pstar_derived"]:
        r = out["policies"][name]
        print(f"\n[{name}]")
        for w in ["train","oos","all"]:
            m=r[w]
            print(f"  {w:5s} days{m['days']:>3} 出票{m['ticket_days']:>3}(0票{m['zero_day_pct']:4.1f}%) "
                  f"avg{m['avg_cohort_return_pct']:+.4f} cum{m['cumulative_pct']:+.2f}% MDD{m['max_drawdown_pct']:6.2f}% posDay{m['positive_day_pct']:.1f}%")
    return out, derived


def main():
    tr = build_trades()
    dates = sorted(tr.signal_date.unique()); split = dates[int(len(dates)*TRAIN_FRACTION)-1]
    print(f"simulated candidate-trades n={len(tr)} days={len(dates)} range {dates[0]}..{dates[-1]} split={split}")
    print("combo counts (all):", tr.groupby(['market_base','sentiment']).size().to_dict())
    cmap = combo_score_map(tr, split)
    pol, derived = compare_policies(tr, split)
    rep = {"split": split, "combo_score_map": cmap, "policy_compare": pol}
    (OUT/"regime_scoreline_map.json").write_text(json.dumps(rep,ensure_ascii=False,indent=2,default=str))
    print("\nsaved", OUT/"regime_scoreline_map.json")


if __name__ == "__main__":
    main()
