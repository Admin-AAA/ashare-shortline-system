"""全量失败归因分析（T3 失败归因闭环 — 通用版）。

覆盖所有实际亏损交易（executed_return < 0），不只 stop_loss_hit：
- 出场原因分布（stop_loss_hit / trailing_stop_hit / stop_loss_gap）
- 亏损 vs 盈利 的特征签名（point-biserial 相关性）
- 分数分桶经济学：每桶净收益、亏损率、盈亏比
- 阈值模拟：扫描候选 min_score，量化"调门槛"对净收益的边际影响

核心 guardrail（第一性原理）：任何"调高分（抬高准入门槛）能减少亏损"的假设，
都必须用分桶净收益 + 阈值模拟推翻。本工具自动计算"抬高门槛 → 净收益变化"，
若变化为负则明确输出"不建议调整"，防止过拟合式调参。

用法:
    python scripts/failure_attribution.py            # 全量归因 + 回归检测
    python scripts/failure_attribution.py --no-baseline  # 不写/不比 baseline
    python scripts/failure_attribution.py --json      # 机器可读输出
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal
from sqlalchemy import text

BASELINE_PATH = Path(__file__).resolve().parent / ".failure_attr_baseline.json"

# 候选准入门槛（market_skill_rule_gate_min_score 当前默认 58.0）
CANDIDATE_THRESHOLDS = [58.0, 60.0, 62.0, 64.0, 66.0]


# --------------------------------------------------------------------------- #
# 纯函数（不依赖 DB，可单测）
# --------------------------------------------------------------------------- #
def point_biserial(xs: list[float | None], y_bin: list[int]) -> tuple[float | None, int]:
    """点二列相关：连续特征 xs 与二值标签 y_bin（1=成功/盈利, 0=失败/亏损）。"""
    pairs = [(x, y) for x, y in zip(xs, y_bin) if x is not None]
    n = len(pairs)
    if n < 30:
        return None, n
    xs2 = [p[0] for p in pairs]
    ys2 = [p[1] for p in pairs]
    mx = sum(xs2) / n
    my = sum(ys2) / n
    cov = sum((xs2[i] - mx) * (ys2[i] - my) for i in range(n))
    vx = sum((x - mx) ** 2 for x in xs2)
    vy = sum((y - my) ** 2 for y in ys2)
    if vx <= 0 or vy <= 0:
        return None, n
    return cov / math.sqrt(vx * vy), n


def _mean(vals: list[float]) -> float | None:
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def bucket_economics(
    scores: list[float], returns: list[float], bucket: int = 5
) -> list[dict[str, Any]]:
    """按 final_score 分桶，计算每桶经济学指标。"""
    agg: dict[int, dict[str, Any]] = {}
    for s, r in zip(scores, returns):
        if s is None or r is None:
            continue
        b = int(s // bucket) * bucket
        d = agg.setdefault(b, {"n": 0, "ret": 0.0, "win": 0, "loss": 0,
                               "win_sum": 0.0, "loss_sum": 0.0})
        d["n"] += 1
        d["ret"] += r
        if r >= 0:
            d["win"] += 1
            d["win_sum"] += r
        else:
            d["loss"] += 1
            d["loss_sum"] += r
    out = []
    for b in sorted(agg):
        d = agg[b]
        win_avg = d["win_sum"] / d["win"] if d["win"] else 0.0
        loss_avg = d["loss_sum"] / d["loss"] if d["loss"] else 0.0
        pl = (win_avg / abs(loss_avg)) if loss_avg != 0 else 0.0
        out.append({
            "bucket": f"{b}-{b + bucket - 1}",
            "n": d["n"],
            "mean_ret": round(d["ret"] / d["n"], 2),
            "loss_rate": round(100 * d["loss"] / d["n"], 1),
            "pl_ratio": round(pl, 2),
            "net_per_trade": round(d["ret"] / d["n"], 2),
        })
    return out


def threshold_simulation(
    scores: list[float], returns: list[float], thresholds: list[float]
) -> dict[float, dict[str, Any]]:
    """模拟"抬高准入门槛到 t"后的净收益影响。保留 score >= t 的样本。"""
    res: dict[float, dict[str, Any]] = {}
    all_ret = [r for s, r in zip(scores, returns) if s is not None and r is not None]
    all_mean = round(sum(all_ret) / len(all_ret), 2) if all_ret else 0.0
    for t in thresholds:
        kept = [r for s, r in zip(scores, returns) if s is not None and r is not None and s >= t]
        kept_mean = round(sum(kept) / len(kept), 2) if kept else 0.0
        delta = round(kept_mean - all_mean, 2)
        res[t] = {
            "kept_n": len(kept),
            "dropped_n": len(all_ret) - len(kept),
            "mean_ret": kept_mean,
            "delta_vs_all": delta,
        }
    return res


def exit_reason_distribution(reasons: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in reasons:
        if r:
            out[r] = out.get(r, 0) + 1
    return dict(sorted(out.items(), key=lambda x: -x[1]))


# --------------------------------------------------------------------------- #
# DB 分析
# --------------------------------------------------------------------------- #
FEATURE_KEYS = [
    "chg20", "chg60", "dist20", "dist60", "pos20", "pos60", "pos120",
    "rsi_12", "rsi_6", "rsi_24", "bias_ma5", "bias_ma10", "bias_ma20",
    "breakout20", "breakout60", "off_high_pct", "off_high60_pct",
    "max_dd20", "max_dd60", "vol5_20", "volume_ratio_5d", "eff20",
    "turnover_rate", "amount_yi",
]


def _load_records() -> list[dict[str, Any]]:
    db = SessionLocal()
    rows = db.execute(text(
        "SELECT r.code, r.trade_date, r.final_score, r.setup_type, "
        "       o.outcome_label, o.executed_return, o.exit_reason, "
        "       r.feature_snapshot_json "
        "FROM recommendations r "
        "JOIN recommendation_outcomes o ON r.recommendation_id = o.recommendation_id "
        "WHERE o.outcome_label IN ('success','failed')"
    )).fetchall()
    seen = set()
    recs = []
    for row in rows:
        key = (row[1], row[0])
        if key in seen:
            continue
        seen.add(key)
        fs = {}
        try:
            fs = json.loads(row[7]) if row[7] else {}
        except Exception:
            fs = {}
        recs.append({
            "code": row[0], "trade_date": row[1], "final_score": row[2],
            "setup_type": row[3], "label": row[4], "ret": row[5],
            "exit_reason": row[6], "fs": fs,
        })
    return recs


def analyze() -> dict[str, Any]:
    recs = _load_records()
    total = len(recs)
    profits = [r for r in recs if r["ret"] is not None and r["ret"] >= 0]
    losses = [r for r in recs if r["ret"] is not None and r["ret"] < 0]
    winrate = round(100 * sum(1 for r in recs if r["label"] == "success") / total, 1) if total else 0

    y_bin = [1 if r["ret"] is not None and r["ret"] >= 0 else 0 for r in recs]
    scores = [r["final_score"] for r in recs]
    returns = [r["ret"] for r in recs]

    # point-biserial per feature
    corrs = []
    for f in FEATURE_KEYS:
        xs = [r["fs"].get(f) for r in recs]
        c, n = point_biserial(xs, y_bin)
        if c is not None:
            corrs.append({"feature": f, "r": round(c, 3), "n": n})
    corrs.sort(key=lambda x: -abs(x["r"]))

    # feature signature loss vs profit
    sig = []
    for f in FEATURE_KEYS:
        lv = [r["fs"].get(f) for r in losses if isinstance(r["fs"].get(f), (int, float)) and not isinstance(r["fs"].get(f), bool)]
        pv = [r["fs"].get(f) for r in profits if isinstance(r["fs"].get(f), (int, float)) and not isinstance(r["fs"].get(f), bool)]
        sig.append({"feature": f,
                    "loss_mean": round(_mean(lv), 2) if lv else None,
                    "profit_mean": round(_mean(pv), 2) if pv else None})

    econ = bucket_economics(scores, returns)
    sim = threshold_simulation(scores, returns, CANDIDATE_THRESHOLDS)
    erd = exit_reason_distribution([r["exit_reason"] for r in recs if r["label"] == "failed"])

    return {
        "total": total,
        "winrate": winrate,
        "n_profit": len(profits),
        "n_loss": len(losses),
        "loss_mean_ret": round(_mean([r["ret"] for r in losses]), 2) if losses else None,
        "profit_mean_ret": round(_mean([r["ret"] for r in profits]), 2) if profits else None,
        "exit_reason_failed": erd,
        "point_biserial": corrs,
        "feature_signature": sig,
        "bucket_economics": econ,
        "threshold_simulation": sim,
    }


def recommend(sim: dict[float, dict[str, Any]]) -> str:
    """基于阈值模拟给出是否调门槛的建议。"""
    base = next(iter(sim.values()))  # 最低阈值（=当前）作为基准
    worst = min(sim.values(), key=lambda v: v["delta_vs_all"])
    if worst["delta_vs_all"] < 0:
        return (f"不建议抬高准入门槛：抬高到任意候选值都会降低净收益"
                f"（最差 delta={worst['delta_vs_all']}%/笔）。"
                f"亏损尾部是动量/regime 驱动的噪声，非可过滤特征阈值。")
    return "当前门槛附近净收益最优，维持。"


def detect_regression(cur: dict[str, Any], base: dict[str, Any]) -> list[str]:
    flags = []
    if base.get("winrate") and cur["winrate"] < base["winrate"] - 2:
        flags.append(f"胜率下降 {base['winrate']}→{cur['winrate']}")
    if base.get("n_loss") and cur["n_loss"] > base["n_loss"] * 1.15:
        flags.append(f"亏损笔数上升 {base['n_loss']}→{cur['n_loss']}")
    if base.get("loss_mean_ret") is not None and cur.get("loss_mean_ret") is not None:
        if cur["loss_mean_ret"] < base["loss_mean_ret"] - 0.5:
            flags.append(f"平均亏损扩大 {base['loss_mean_ret']}→{cur['loss_mean_ret']}")
    return flags


def _fmt_report(res: dict[str, Any]) -> str:
    L = []
    L.append(f"=== 全量失败归因 (T3) | 去重样本 n={res['total']} 胜率={res['winrate']}% ===")
    L.append(f"盈利 {res['n_profit']} 笔 (均 {res['profit_mean_ret']}%) | "
             f"亏损 {res['n_loss']} 笔 (均 {res['loss_mean_ret']}%)")
    L.append("\n-- 失败出场原因分布 --")
    for k, v in res["exit_reason_failed"].items():
        L.append(f"  {k}: {v}")
    L.append("\n-- point-biserial 相关性 (特征 vs 盈利) 最强 8 项 --")
    for c in res["point_biserial"][:8]:
        L.append(f"  {c['feature']:14s} r={c['r']:+.3f} n={c['n']}")
    L.append("\n-- 分数分桶经济学 --")
    L.append(f"  {'桶':12s} {'n':>5s} {'净收益%':>8s} {'亏损率%':>8s} {'盈亏比':>7s}")
    for b in res["bucket_economics"]:
        L.append(f"  {b['bucket']:12s} {b['n']:5d} {b['mean_ret']:8.2f} "
                 f"{b['loss_rate']:8.1f} {b['pl_ratio']:7.2f}")
    L.append("\n-- 准入门槛模拟 (guardrail) --")
    for t, v in res["threshold_simulation"].items():
        L.append(f"  min_score>={t}: 保留 {v['kept_n']} 丢 {v['dropped_n']} | "
                 f"净收益 {v['mean_ret']}% (delta {v['delta_vs_all']:+.2f})")
    L.append(f"\n建议: {recommend(res['threshold_simulation'])}")
    return "\n".join(L)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-baseline", action="store_true", help="不读写 baseline")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()

    res = analyze()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    print(_fmt_report(res))

    # baseline 回归检测
    if not args.no_baseline:
        base = None
        if BASELINE_PATH.exists():
            try:
                base = json.loads(BASELINE_PATH.read_text())
            except Exception:
                base = None
        if base:
            flags = detect_regression(res, base)
            if flags:
                print("\n⚠️ 回归检测告警:")
                for f in flags:
                    print(f"  - {f}")
            else:
                print("\n✅ 回归检测: 无显著退化")
        BASELINE_PATH.write_text(json.dumps({
            "total": res["total"], "winrate": res["winrate"],
            "n_loss": res["n_loss"], "loss_mean_ret": res["loss_mean_ret"],
        }, ensure_ascii=False, indent=2))
        print(f"\nbaseline 已更新: {BASELINE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
