"""
LLM Skill Agent 实际贡献 A/B 分析脚本

问题一：LLM score 高低是否预测 outcome？
问题二：Veto 穿透到推荐的股票，真拦截会影响什么？
问题三：LLM score vs Rule score 预测力对比（Gini 系数/AUC）
问题四：score_delta bug 修复前后（clamp vs 未 clamp）的分数分布差异

用法：
    cd /Users/wangao/Desktop/ashare-shortline-system
    .venv/bin/python backend/scripts/llm_ab_analysis.py
"""

import json
import sys
from typing import Any

sys.path.insert(0, "backend")

from sqlalchemy import text
from app.db.session import SessionLocal


def _wr(wins: int, total: int) -> str:
    if total == 0:
        return "N/A"
    return f"{wins * 100.0 / total:.1f}%"


def section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)


def main() -> None:
    db = SessionLocal()
    try:
        run(db)
    finally:
        db.close()


def run(db: Any) -> None:
    # ── 1. LLM score 分段 vs outcome ──────────────────────────────
    section("1. LLM score 分段 vs outcome（越高分越赢？）")

    rows = db.execute(text("""
        SELECT
          lrc.code,
          lrc.trade_date,
          CAST(JSON_EXTRACT(lrc.review_json, '$.score') AS UNSIGNED) AS llm_score,
          JSON_UNQUOTE(JSON_EXTRACT(lrc.review_json, '$.stance')) AS stance,
          CAST(JSON_EXTRACT(lrc.review_json, '$.confidence') AS DECIMAL(4,3)) AS confidence,
          ro.outcome_label,
          ro.close_return
        FROM llm_review_cache lrc
        JOIN recommendations r
          ON r.code = lrc.code AND r.trade_date = lrc.trade_date
        JOIN recommendation_outcomes ro
          ON ro.recommendation_id = r.recommendation_id
        WHERE lrc.created_at >= '2026-06-01'
          AND ro.outcome_label IN ('success', 'failed')
    """)).fetchall()

    buckets: dict[str, list] = {
        "score<50": [],
        "50-59":    [],
        "60-69":    [],
        "70-79":    [],
        "80-89":    [],
        "score>=90":[],
    }
    llm_scores = []
    outcomes = []

    for row in rows:
        score = int(row.llm_score or 50)
        outcome = row.outcome_label
        ret = float(row.close_return or 0)
        llm_scores.append(score)
        outcomes.append(1 if outcome == "success" else 0)
        if score < 50:
            buckets["score<50"].append((outcome, ret))
        elif score < 60:
            buckets["50-59"].append((outcome, ret))
        elif score < 70:
            buckets["60-69"].append((outcome, ret))
        elif score < 80:
            buckets["70-79"].append((outcome, ret))
        elif score < 90:
            buckets["80-89"].append((outcome, ret))
        else:
            buckets["score>=90"].append((outcome, ret))

    print(f"{'分段':<12} {'样本':>6} {'胜率':>8} {'平均收益':>10}")
    print("-" * 40)
    for label, items in buckets.items():
        if not items:
            continue
        n = len(items)
        wins = sum(1 for o, _ in items if o == "success")
        avg_ret = sum(r for _, r in items) / n
        print(f"{label:<12} {n:>6} {_wr(wins, n):>8} {avg_ret*100:>9.2f}%")

    # ── 2. Score delta 修复影响评估（clamp vs 未 clamp）────────────
    section("2. score_delta clamp 修复：受影响的历史 cache 条目")

    delta_rows = db.execute(text("""
        SELECT
          CAST(JSON_EXTRACT(review_json, '$.score') AS UNSIGNED) AS llm_score,
          ROUND(CAST(JSON_EXTRACT(review_json, '$.score') AS UNSIGNED) - 50, 2) AS unclamped_delta,
          ROUND(
            GREATEST(-18, LEAST(16,
              CAST(JSON_EXTRACT(review_json, '$.score') AS UNSIGNED) - 50
            )), 2
          ) AS clamped_delta,
          JSON_UNQUOTE(JSON_EXTRACT(review_json, '$.stance')) AS stance,
          COUNT(*) AS cnt
        FROM llm_review_cache
        WHERE created_at >= '2026-06-01'
          AND ABS(
            CAST(JSON_EXTRACT(review_json, '$.score') AS UNSIGNED) - 50
            - GREATEST(-18, LEAST(16,
                CAST(JSON_EXTRACT(review_json, '$.score') AS UNSIGNED) - 50
              ))
          ) > 0.01
        GROUP BY llm_score, stance
        ORDER BY cnt DESC
        LIMIT 20
    """)).fetchall()

    if delta_rows:
        print(f"{'stance':<20} {'llm_score':>10} {'未 clamp delta':>15} {'clamp 后 delta':>15} {'条数':>6}")
        print("-" * 70)
        for row in delta_rows:
            print(f"{str(row.stance):<20} {int(row.llm_score or 0):>10} "
                  f"{float(row.unclamped_delta or 0):>15.2f} "
                  f"{float(row.clamped_delta or 0):>15.2f} "
                  f"{int(row.cnt):>6}")
    else:
        print("  所有 cache 条目 score 均在 [32, 66] 范围内（delta 不会超出 [-18, 16]），无影响。")

    # ── 3. Veto 穿透分析：若硬拦截 veto 会怎样？─────────────────────
    section("3. Veto 穿透分析（461 次 veto 中有多少进了推荐）")

    veto_rows = db.execute(text("""
        SELECT
          ro.outcome_label,
          ro.close_return,
          r.final_score,
          lrc.code
        FROM llm_review_cache lrc
        JOIN recommendations r
          ON r.code = lrc.code AND r.trade_date = lrc.trade_date
        JOIN recommendation_outcomes ro
          ON ro.recommendation_id = r.recommendation_id
        WHERE lrc.created_at >= '2026-06-01'
          AND JSON_UNQUOTE(JSON_EXTRACT(lrc.review_json, '$.stance')) = 'veto'
          AND ro.outcome_label IN ('success', 'failed')
    """)).fetchall()

    total_veto = len(veto_rows)
    veto_wins = sum(1 for r in veto_rows if r.outcome_label == "success")
    veto_avg_ret = sum(float(r.close_return or 0) for r in veto_rows) / max(total_veto, 1)

    print(f"  Veto 但仍进推荐且已结算：{total_veto} 条")
    print(f"  胜率：{_wr(veto_wins, total_veto)}  平均收益：{veto_avg_ret*100:.2f}%")
    print(f"\n  若硬拦截 veto（移除这 {total_veto} 条）：")
    print(f"    → 避免了 {total_veto - veto_wins} 次亏损，同时丢弃了 {veto_wins} 次胜利")
    print(f"    → 净胜局变化：{veto_wins - (total_veto - veto_wins):+d} 局")

    # ── 4. LLM score vs Rule score 预测力对比（简单相关性） ──────────
    section("4. LLM score vs Rule score 对 outcome 的相关性")

    corr_rows = db.execute(text("""
        SELECT
          CAST(JSON_EXTRACT(lrc.review_json, '$.score') AS DECIMAL(6,2)) AS llm_score,
          r.final_score,
          CASE WHEN ro.outcome_label='success' THEN 1 ELSE 0 END AS win
        FROM llm_review_cache lrc
        JOIN recommendations r
          ON r.code = lrc.code AND r.trade_date = lrc.trade_date
        JOIN recommendation_outcomes ro
          ON ro.recommendation_id = r.recommendation_id
        WHERE lrc.created_at >= '2026-06-01'
          AND ro.outcome_label IN ('success', 'failed')
          AND JSON_EXTRACT(lrc.review_json, '$.score') IS NOT NULL
    """)).fetchall()

    if corr_rows:
        import statistics
        llm_s = [float(r.llm_score or 50) for r in corr_rows]
        rule_s = [float(r.final_score or 50) for r in corr_rows]
        wins = [int(r.win) for r in corr_rows]
        n = len(wins)
        mean_win = sum(wins) / n

        def pearson(xs: list, ys: list) -> float:
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
            return num / max(den, 1e-9)

        r_llm = pearson(llm_s, wins)
        r_rule = pearson(rule_s, wins)

        print(f"  样本量：{n}")
        print(f"  LLM score 与 outcome 的 Pearson 相关：{r_llm:.4f}")
        print(f"  Final score  与 outcome 的 Pearson 相关：{r_rule:.4f}")
        print(f"\n  解读：{'LLM score 更强' if abs(r_llm) > abs(r_rule) else 'Final score 更强（规则为主）'}")
        print(f"  （正值=分越高越容易赢，越接近0越没有预测力）")

        # 按分段看 LLM 是否有区分度
        print(f"\n  LLM score 四分位 vs 胜率：")
        sorted_pairs = sorted(zip(llm_s, wins))
        q = n // 4
        quarters = [sorted_pairs[:q], sorted_pairs[q:2*q], sorted_pairs[2*q:3*q], sorted_pairs[3*q:]]
        for i, group in enumerate(quarters):
            g_wins = sum(w for _, w in group)
            g_scores = [s for s, _ in group]
            print(f"    Q{i+1} [{min(g_scores):.0f}-{max(g_scores):.0f}]: "
                  f"样本={len(group)}, 胜率={_wr(g_wins, len(group))}")
    else:
        print("  无足够数据")

    # ── 5. 总结 ──────────────────────────────────────────────────
    section("5. 结论摘要")
    print(f"  1. score_delta clamp 修复：检查上方表格是否有受影响条目")
    print(f"  2. LLM score 分段胜率：是否存在单调递增的趋势？")
    print(f"     → 若无明显趋势（各段胜率接近），LLM 评分无预测力")
    print(f"     → 若 score>=70 段明显高于 score<60 段，LLM 有贡献")
    print(f"  3. Veto 拦截是否值得：")
    print(f"     → 若 veto 胜率 > 全体胜率（约 76%），硬拦截会降低系统表现")
    print(f"     → 若 veto 胜率 < 50%，硬拦截可提升系统表现")


if __name__ == "__main__":
    main()
