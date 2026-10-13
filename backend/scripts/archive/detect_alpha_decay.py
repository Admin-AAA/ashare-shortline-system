"""Alpha 衰减检测：对比关键因子在前期 vs 近期的 point-biserial 相关系数。

第一性原理：市场会学习并套利掉公开因子，alpha 必然衰减。定期检测因子的预测力
是否在下降，衰减 >30% 标记为"衰减中"。

注意：point-biserial 衰减不等于因子无效——阈值效应的因子（如 dist60>=9.4 加分）
可能 point-biserial 衰减但阈值筛选仍有效。衰减检测结果需配合反事实验证。

用法：
    python -m scripts.detect_alpha_decay          # 检测所有关键因子
    python -m scripts.detect_alpha_decay --factor dist60  # 单因子 + 反事实
"""
from __future__ import annotations

import argparse
import json
from math import sqrt

from app.db.session import SessionLocal
from sqlalchemy import text


KEY_FACTORS = [
    "breakout60", "dist60", "breakout20", "pos20", "chip_concentration",
    "bias_ma5", "avg_cost", "capital_resonance_score", "is_main_theme",
]


def point_biserial(values: list[float], labels: list[int]) -> float | None:
    """计算 point-biserial 相关系数。labels: 0/1 (failed/success)。"""
    n = len(values)
    if n < 10:
        return None
    n1 = sum(labels)
    n0 = n - n1
    if n1 == 0 or n0 == 0:
        return None
    mean1 = sum(v for v, l in zip(values, labels) if l == 1) / n1
    mean0 = sum(v for v, l in zip(values, labels) if l == 0) / n0
    mean_all = sum(values) / n
    ss = sum((v - mean_all) ** 2 for v in values)
    if ss == 0:
        return None
    return (mean1 - mean0) * sqrt(n1 * n0 / n) / sqrt(ss)


def _extract_factor(fs: dict, factor: str):
    """从 feature_snapshot 取因子值，支持 legacy_stock 嵌套。"""
    val = fs.get(factor)
    if val is None:
        val = (fs.get("legacy_stock") or {}).get(factor)
    return val


def load_samples(db):
    """加载去重的 formal 推荐样本（trade_date, code 去重）。"""
    rows = db.execute(text("""
        SELECT rec.trade_date, rec.code, rec.feature_snapshot_json, o.outcome_label
        FROM recommendations rec
        JOIN recommendation_runs ru ON ru.run_id = rec.run_id
        LEFT JOIN recommendation_outcomes o ON o.recommendation_id = rec.recommendation_id
        WHERE ru.run_mode = 'formal' AND o.outcome_label IN ('success', 'failed')
        ORDER BY rec.trade_date ASC
    """)).fetchall()
    seen: set[tuple[str, str]] = set()
    samples: list[tuple[str, dict, int]] = []
    for row in rows:
        key = (row[0], row[1])
        if key in seen:
            continue
        seen.add(key)
        fs = json.loads(row[2]) if row[2] else {}
        label = 1 if row[3] == "success" else 0
        samples.append((row[0], fs, label))
    return samples


def detect_decay(db, factors: list[str] | None = None) -> list[dict]:
    """检测因子衰减，返回 [{factor, pb_early, pb_late, decay_pct, status}]。"""
    factors = factors or KEY_FACTORS
    samples = load_samples(db)
    if not samples:
        return []
    mid = len(samples) // 2
    early = samples[:mid]
    late = samples[mid:]
    results = []
    for f in factors:
        e_data = [(_extract_factor(fs, f) or 0, l) for _, fs, l in early]
        l_data = [(_extract_factor(fs, f) or 0, l) for _, fs, l in late]
        if len(e_data) < 10 or len(l_data) < 10:
            continue
        pb_e = point_biserial([v for v, _ in e_data], [l for _, l in e_data])
        pb_l = point_biserial([v for v, _ in l_data], [l for _, l in l_data])
        if pb_e is None or pb_l is None:
            continue
        decay = (1 - abs(pb_l) / abs(pb_e)) * 100 if abs(pb_e) > 0.01 else 0
        status = "⚠️衰减" if decay > 30 else "稳定"
        results.append({
            "factor": f, "pb_early": round(pb_e, 3), "pb_late": round(pb_l, 3),
            "decay_pct": round(decay, 1), "status": status,
            "early_range": f"{early[0][0]}~{early[-1][0]}",
            "late_range": f"{late[0][0]}~{late[-1][0]}",
        })
    return results


def counterfactual_factor(db, factor: str, threshold: float, bonus: float) -> dict:
    """反事实：移除因子加分后，多少推荐掉出分数线，掉出样本的胜率。"""
    rows = db.execute(text("""
        SELECT rec.feature_snapshot_json, rec.final_score, o.outcome_label
        FROM recommendations rec
        JOIN recommendation_runs ru ON ru.run_id = rec.run_id
        LEFT JOIN recommendation_outcomes o ON o.recommendation_id = rec.recommendation_id
        WHERE ru.run_mode = 'formal' AND o.outcome_label IN ('success', 'failed')
    """)).fetchall()
    seen: set = set()
    samples = []
    for row in rows:
        fs = json.loads(row[0]) if row[0] else {}
        val = _extract_factor(fs, factor)
        if val is None:
            continue
        key = (fs.get("code"), fs.get("trade_date"))
        if key in seen:
            continue
        seen.add(key)
        meta = fs.get("score_line_meta") or {}
        eff_line = meta.get("effective_score_line")
        if eff_line is None:
            continue
        label = 1 if row[2] == "success" else 0
        samples.append((float(val), row[1], float(eff_line), label))
    high = [s for s in samples if s[0] >= threshold]
    would_drop = [s for s in high if s[1] - bonus < s[2]]
    drop_labels = [s[3] for s in would_drop]
    total = len(samples)
    total_wins = sum(s[3] for s in samples)
    return {
        "factor": factor, "threshold": threshold, "bonus": bonus,
        "high_count": len(high),
        "would_drop": len(would_drop),
        "drop_winrate": round(sum(drop_labels) / len(drop_labels) * 100, 1) if drop_labels else 0,
        "overall_winrate": round(total_wins / total * 100, 1) if total else 0,
        "after_removal_winrate": round((total_wins - sum(drop_labels)) / max(1, total - len(would_drop)) * 100, 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--factor", help="单因子反事实验证（如 dist60）")
    parser.add_argument("--threshold", type=float, default=9.4, help="因子阈值（默认 9.4）")
    parser.add_argument("--bonus", type=float, default=6.0, help="加分值（默认 6.0）")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.factor:
            print(f"=== {args.factor} 反事实验证（阈值>={args.threshold}, 加分={args.bonus}）===")
            r = counterfactual_factor(db, args.factor, args.threshold, args.bonus)
            print(f"  高分位: {r['high_count']} 笔")
            print(f"  移除加分后掉出分数线: {r['would_drop']} 笔 (胜率 {r['drop_winrate']}%)")
            print(f"  整体胜率: {r['overall_winrate']}% → 移除后 {r['after_removal_winrate']}%")
        else:
            print("=== Alpha 衰减检测 ===")
            results = detect_decay(db)
            if not results:
                print("无样本")
                return
            print(f"{'因子':<28} {'前期':>8} {'近期':>8} {'衰减%':>8} {'状态':>8}")
            for r in results:
                print(f"{r['factor']:<28} {r['pb_early']:>8.3f} {r['pb_late']:>8.3f} {r['decay_pct']:>7.1f}% {r['status']:>8}")
            print(f"\n前期: {results[0]['early_range']}")
            print(f"近期: {results[0]['late_range']}")
            decayed = [r for r in results if r["status"] == "⚠️衰减"]
            if decayed:
                print(f"\n⚠️ 衰减因子: {', '.join(r['factor'] for r in decayed)}")
                print("注意：point-biserial 衰减不等于因子无效，需配合反事实验证（--factor）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
