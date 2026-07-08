#!/usr/bin/env python3
"""self-improve 每轮自检脚本：输出 A/B/C/D/E 五项北极星指标摘要。

用法：
    cd backend && source .venv/bin/activate && python3 ../scripts/self_check.py
"""

import json
import os
import sys

# 让 backend/ 下的 app 包可被导入
BACKEND_DIR = os.path.join(os.path.dirname(__file__), '..', 'backend')
sys.path.insert(0, os.path.abspath(BACKEND_DIR))

from app.db.session import SessionLocal
from sqlalchemy import text

SKILLS_DIR = os.path.join(os.path.dirname(__file__), '..', 'backend', 'skills')


def check_a_item(db) -> None:
    """A 项：最近 7 天 formal 推荐产出（按交易日聚合，标出断档日）。"""
    rows = db.execute(text("""
        SELECT trade_date, run_mode, status, recommended_count
        FROM recommendation_runs
        WHERE run_mode='formal' AND started_at > DATE_SUB(NOW(), INTERVAL 7 DAY)
        ORDER BY started_at DESC
    """)).fetchall()

    print('=== 最近 7 天 formal run ===')
    for row in rows:
        print(f'  {row[0]} {row[1]} {row[2]} rec_n={row[3]}')

    # 按 trade_date 聚合：某日只要有 completed+rec_n>0 即算该日有产出
    by_date: dict[str, bool] = {}
    for r in rows:
        td = str(r[0])
        has_ok = r[2] == 'completed' and (r[3] or 0) > 0
        by_date[td] = by_date.get(td, False) or has_ok

    ok_dates = sorted(d for d, ok in by_date.items() if ok)
    # 断档日 = 系统跑过 formal run 但当日无任何成功推荐（排除周末/假日——那几天没有 run）
    gap_dates = sorted(d for d, ok in by_date.items() if not ok)
    print(f'A 项：最近 7 天有 {len(ok_dates)} 个交易日产出 formal 推荐（目标 ≥5）')
    if gap_dates:
        print(f'⚠️ {len(gap_dates)} 个交易日系统跑了但 0 推荐（T12 排查: prefilter 过滤/数据缺失）: {gap_dates}')

    interrupted = [r for r in rows if r[2] == 'interrupted']
    if interrupted:
        print(f'⚠️ {len(interrupted)} 个 interrupted run')


def check_b_item(db) -> None:
    """B 项：最近推荐字段完整性（五项决策链：策略名+核心因子+入场/失效+LLM delta）。"""
    row = db.execute(text("""
        SELECT final_score, bucket, setup_type, entry_rule, invalid_rule,
               JSON_EXTRACT(feature_snapshot_json, '$.avg_cost')   AS avg_cost,
               JSON_EXTRACT(agent_summary_json,  '$.score_delta')  AS llm_delta,
               JSON_EXTRACT(agent_summary_json,  '$.stance')       AS llm_stance
        FROM recommendations
        ORDER BY recommended_at DESC
        LIMIT 1
    """)).fetchone()

    if not row:
        print('B 项：无推荐记录')
        return

    fields = {
        '策略名(bucket)':     row[1],
        '核心因子(avg_cost)': row[5],
        '入场规则':           row[3],
        '失效规则':           row[4],
        'LLM delta':          row[6],
    }
    missing = [k for k, v in fields.items() if not v]
    missing_str = str(missing) if missing else '无'
    print(
        f'B 项：score={row[0]} bucket={row[1]} setup={row[2]} '
        f'stance={row[7]} llm_delta={row[6]} 缺失={missing_str}'
    )


def check_c_item(db) -> set:
    """C 项：策略命中覆盖。返回已命中策略集合供 E 项使用。"""
    rows = db.execute(text("""
        SELECT JSON_EXTRACT(skill_breakdown_json, '$[*].strategy_id') AS skills
        FROM recommendations r
        JOIN recommendation_runs u ON r.run_id = u.run_id
        WHERE u.run_mode = 'formal' AND r.skill_breakdown_json IS NOT NULL
    """)).fetchall()

    skills: set = set()
    for row in rows:
        try:
            arr = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            if isinstance(arr, list):
                skills.update(arr)
        except Exception:
            pass

    print(f'C 项：formal 命中策略 {len(skills)} 个: {sorted(skills)}')
    return skills


def check_e_item(db, hit_skills: set) -> None:
    """E 项：策略覆盖盲区 + 低胜率标记。"""
    # 覆盖盲区
    registered = sorted(
        d for d in os.listdir(SKILLS_DIR)
        if os.path.isdir(os.path.join(SKILLS_DIR, d))
    )
    blind = [s for s in registered if s not in hit_skills]
    print(f'E 项覆盖盲区：{len(blind)} 个策略近 60 天 formal 0 命中: {blind}')

    # 低覆盖 / 低胜率策略
    rows = db.execute(text("""
        WITH strategy_hits AS (
            SELECT jt.strategy_id, o.outcome_label, o.executed_return
            FROM recommendations r
            JOIN recommendation_runs u ON r.run_id = u.run_id
            LEFT JOIN recommendation_outcomes o ON o.recommendation_id = r.recommendation_id
            JOIN JSON_TABLE(
                r.skill_breakdown_json, '$[*]'
                COLUMNS (strategy_id VARCHAR(80) PATH '$.strategy_id')
            ) jt
            WHERE r.skill_breakdown_json IS NOT NULL
              AND o.outcome_label IN ('success', 'failed')
        )
        SELECT strategy_id,
               COUNT(*)                                                          AS n,
               SUM(CASE WHEN outcome_label = 'success' THEN 1 ELSE 0 END)       AS wins,
               ROUND(AVG(executed_return) * 100, 2)                             AS avg_ret
        FROM strategy_hits
        GROUP BY strategy_id
        HAVING n < 50 OR wins * 100 / COUNT(*) < 45
        ORDER BY wins * 100 / COUNT(*) ASC
    """)).fetchall()

    print('E 项低覆盖(n<50)或低胜率(<45%)策略:')
    for row in rows:
        n, wins, avg_ret = row[1], row[2], row[3]
        win_rate = round(wins * 100 / n, 1)
        tag = '低胜率' if win_rate < 45 else '低覆盖'
        print(f'  {row[0]}: n={n} wins={wins} avg_ret={avg_ret}% winrate={win_rate}% ←{tag}需标记')


def check_d_item(db) -> None:
    """D 项：核心指标（胜率 + 最大亏损），自动对比基线判断方向。"""
    WIN_RATE_BASELINE = 61.5
    MAX_LOSS_BASELINE = -5.80

    row = db.execute(text("""
        SELECT COUNT(*),
               SUM(CASE WHEN outcome_label = 'success' THEN 1 ELSE 0 END)
        FROM recommendation_outcomes
        WHERE outcome_label IN ('success', 'failed')
    """)).fetchone()

    if row and row[0] > 0:
        win_rate = round(float(row[1]) * 100 / float(row[0]), 1)
        delta = round(win_rate - WIN_RATE_BASELINE, 1)
        tag = f'⚠️ 下滑 {abs(delta)}pp' if delta < 0 else f'↑ 高于基线 {delta}pp'
        print(f'D 项：胜率 {win_rate}% （基线 {WIN_RATE_BASELINE}%，{tag}）')

    row = db.execute(text("""
        SELECT ROUND(MIN(executed_return), 2)
        FROM recommendation_outcomes
        WHERE outcome_label IN ('success', 'failed')
    """)).fetchone()

    if row and row[0] is not None:
        min_ret = float(row[0])
        worsened = min_ret < MAX_LOSS_BASELINE
        tag = f'⚠️ 恶化 {round(min_ret - MAX_LOSS_BASELINE, 2)}pp' if worsened else f'优于基线'
        print(f'D 项：最大亏损 {min_ret}% （基线 {MAX_LOSS_BASELINE}%，{tag}）')


def main() -> None:
    db = SessionLocal()
    try:
        check_a_item(db)
        check_b_item(db)
        hit_skills = check_c_item(db)
        check_e_item(db, hit_skills)
        check_d_item(db)
    finally:
        db.close()


if __name__ == '__main__':
    main()
