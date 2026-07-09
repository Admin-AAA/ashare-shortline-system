#!/usr/bin/env python3
"""self-improve 每轮自检脚本：输出 A/B/C/D/E 五项北极星指标摘要。

用法：
    cd backend && source .venv/bin/activate && python3 ../scripts/self_check.py
"""

import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

# 让 backend/ 下的 app 包可被导入
BACKEND_DIR = os.path.join(os.path.dirname(__file__), '..', 'backend')
sys.path.insert(0, os.path.abspath(BACKEND_DIR))

from app.db.session import SessionLocal
from sqlalchemy import text

SKILLS_DIR = os.path.join(os.path.dirname(__file__), '..', 'backend', 'skills')

# 策略规则引擎（用于 D 项"规则覆盖前瞻校验"——区分历史污染 vs 真退化）
# 惰性加载：仅在 check_d_rule_coverage 执行时载入一次，避免 A/B/C/E 项承受导入开销
_SKILLS_CACHE: dict | None = None


def _load_skills_once() -> dict:
    global _SKILLS_CACHE
    if _SKILLS_CACHE is None:
        try:
            from app.engine.skill_loader import load_skills
            _SKILLS_CACHE = {s.skill_id: s for s in load_skills(Path(SKILLS_DIR))}
        except Exception as e:
            # 规则引擎不可用时静默降级（D 项规则覆盖校验会跳过），不影响 A/B/C/D/E 主指标
            print(f'  (规则引擎加载失败，跳过 D 项覆盖校验: {type(e).__name__}: {e})')
            _SKILLS_CACHE = {}
    return _SKILLS_CACHE


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

    # 低覆盖 / 低胜率策略（按 recommendation_id 去重，避免 JSON_TABLE 多策略行重复计数）
    rows = db.execute(text("""
        WITH strategy_hits AS (
            SELECT DISTINCT rec.recommendation_id, jt.strategy_id, o.outcome_label, o.executed_return
            FROM recommendations rec
            JOIN recommendation_runs u ON rec.run_id = u.run_id
            LEFT JOIN recommendation_outcomes o ON o.recommendation_id = rec.recommendation_id
            JOIN JSON_TABLE(
                rec.skill_breakdown_json, '$[*]'
                COLUMNS (strategy_id VARCHAR(80) PATH '$.strategy_id')
            ) jt
            WHERE u.run_mode = 'formal'
              AND rec.skill_breakdown_json IS NOT NULL
              AND o.outcome_label IN ('success', 'failed')
        )
        SELECT strategy_id,
               COUNT(*)                                                          AS n,
               SUM(CASE WHEN outcome_label = 'success' THEN 1 ELSE 0 END)       AS wins,
               ROUND(AVG(executed_return), 2)                                    AS avg_ret
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

    # 曝光公平度 + 样本置信度（近 30 天 formal+experiment，不含 backtest）
    # 目的：让每个策略胜率统计可信，避免头部策略吃掉 80%+ 曝光、尾部策略样本稀疏不可信。
    SAMPLE_FLOOR = 20          # 每策略近 30 天样本下限（统计可信）
    FAIRNESS_TARGET = 0.50     # 归一化熵目标（=1 表示完全均匀，=0 表示全集中一个策略）
    exp_rows = db.execute(text("""
        SELECT rec.recommendation_id, jt.strategy_id
        FROM recommendations rec
        JOIN recommendation_runs u ON rec.run_id = u.run_id
        JOIN JSON_TABLE(
            rec.skill_breakdown_json, '$[*]'
            COLUMNS (strategy_id VARCHAR(80) PATH '$.strategy_id')
        ) jt
        WHERE u.run_mode IN ('formal', 'experiment')
          AND rec.skill_breakdown_json IS NOT NULL
          AND rec.recommended_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
    """)).fetchall()
    # 按 recommendation_id 去重（同条推荐被多策略命中，每个策略各计一次曝光）
    per_rec: dict = {}
    for rid, sid in exp_rows:
        per_rec.setdefault(rid, set()).add(sid)
    exposure: Counter = Counter()
    for sids in per_rec.values():
        for sid in sids:
            exposure[sid] += 1

    print(f'E 项曝光分布（近 30 天 formal+experiment，按推荐去重）:')
    total_exp = sum(exposure.values())
    for sid, n in exposure.most_common():
        share = (n / total_exp * 100) if total_exp else 0
        flag = '⚠️<样本下限' if n < SAMPLE_FLOOR else ''
        print(f'  {sid}: {n} ({share:.1f}%){(" " + flag) if flag else ""}')

    # 归一化熵 H/H_max：衡量曝光集中度。被命中的策略数 = exposure 的 key 数
    k = len(exposure)
    if k > 1 and total_exp > 0:
        entropy = -sum((n / total_exp) * math.log(n / total_exp) for n in exposure.values())
        h_norm = entropy / math.log(k)
    else:
        h_norm = 0.0
    fair_tag = '✅ 达标' if h_norm >= FAIRNESS_TARGET else '⚠️ 过度集中'
    print(f'E 项曝光公平度: H_norm={h_norm:.3f} （目标 ≥{FAIRNESS_TARGET}，{fair_tag}）')

    # 注册策略中近 30 天 0 曝光的（曝光补跑应持续喂到 SAMPLE_FLOOR）
    zero_exp = [s for s in registered if s not in exposure]
    if zero_exp:
        print(f'⚠️ {len(zero_exp)} 个注册策略近 30 天 0 曝光（补跑未覆盖）: {sorted(zero_exp)}')
    under_floor = sorted(s for s, n in exposure.items() if n < SAMPLE_FLOOR)
    if under_floor:
        print(f'⚠️ {len(under_floor)} 个策略样本 <{SAMPLE_FLOOR}（胜率统计不可信，补跑应持续喂）: {under_floor}')


def check_d_item(db) -> None:
    """D 项：核心指标（胜率 + 最大亏损），自动对比基线判断方向。

    只统计真实 LIVE formal 推荐（recommendation_id 以 rec_ 开头）。
    回测（btrec_）与 experiment 补跑不纳入北极星 D 项——它们会虚高胜率、
    掩盖真实 formal 表现（回测污染曾使 D 项胜率从真实 59% 虚报到 68%）。
    """
    WIN_RATE_BASELINE = 61.5
    MAX_LOSS_BASELINE = -5.80

    # 胜率按 (code, trade_date) 去重：同一交易日同一只股票常被多个 formal run 重复推荐
    # （2026-07-08 单日 14 个 run，松发股份被推荐 7 次全部 -5.69% 止损）。这些重复记录
    # 是同一信号的多次存储，outcome 完全一致（LIVE 验证 15 组同股同日 0 组不一致），
    # 按笔数统计会把失败股放大 N 倍扭曲胜率。去重后反映真实独立股票胜率。
    # （2026-07-10 第162轮：去重前 n=302 WR=56.6% 看似下滑，去重后 n=171 WR=60.2% 接近基线）
    row = db.execute(text("""
        WITH dedup AS (
            SELECT r.code, r.trade_date, MAX(o.outcome_label) AS label
            FROM recommendation_outcomes o
            JOIN recommendations r ON r.recommendation_id = o.recommendation_id
            JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
            WHERE o.outcome_label IN ('success', 'failed')
              AND r.recommendation_id LIKE 'rec_%'
            GROUP BY r.code, r.trade_date
        )
        SELECT COUNT(*),
               SUM(CASE WHEN label = 'success' THEN 1 ELSE 0 END)
        FROM dedup
    """)).fetchone()

    if row and row[0] > 0:
        win_rate = round(float(row[1]) * 100 / float(row[0]), 1)
        delta = round(win_rate - WIN_RATE_BASELINE, 1)
        tag = f'⚠️ 下滑 {abs(delta)}pp' if delta < 0 else f'↑ 高于基线 {delta}pp'
        print(f'D 项：胜率 {win_rate}% （n={row[0]}，基线 {WIN_RATE_BASELINE}%，{tag}）')

    row = db.execute(text("""
        SELECT ROUND(MIN(o.executed_return), 2)
        FROM recommendation_outcomes o
        JOIN recommendations r ON r.recommendation_id = o.recommendation_id
        JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
        WHERE o.outcome_label IN ('success', 'failed')
          AND r.recommendation_id LIKE 'rec_%'
    """)).fetchone()

    if row and row[0] is not None:
        min_ret = float(row[0])
        worsened = min_ret < MAX_LOSS_BASELINE
        tag = f'⚠️ 恶化 {round(min_ret - MAX_LOSS_BASELINE, 2)}pp' if worsened else f'优于基线'
        print(f'D 项：最大亏损 {min_ret}% （基线 {MAX_LOSS_BASELINE}%，{tag}）')

    # 单日集中度诊断（独立股票口径）：防止单日异常高量扭曲 D 项判断。
    # 已改用独立股票数（同股同日多 run 只算 1 只），避免"单日 N 笔"假警报——
    # 2026-07-08 笔口径 126 笔占 71%，独立股票口径仅 36 只占 21%，不再是扭曲信号。
    total_n = db.execute(text("""
        SELECT COUNT(DISTINCT CONCAT(r.code,'_',r.trade_date))
        FROM recommendation_outcomes o
        JOIN recommendations r ON r.recommendation_id = o.recommendation_id
        JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
        WHERE o.outcome_label IN ('success','failed') AND r.recommendation_id LIKE 'rec_%'
    """)).fetchone()[0]
    top_day = db.execute(text("""
        SELECT r.trade_date AS d, COUNT(DISTINCT r.code) AS n
        FROM recommendation_outcomes o
        JOIN recommendations r ON r.recommendation_id = o.recommendation_id
        JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
        WHERE o.outcome_label IN ('success','failed') AND r.recommendation_id LIKE 'rec_%'
        GROUP BY d ORDER BY n DESC LIMIT 1
    """)).fetchone()
    if total_n and top_day:
        share = top_day[1] * 100 / total_n
        flag = (f'⚠️ 单日 {top_day[0]} 独立股票 {share:.0f}%（D 项可能被单日集中扭曲，'
                f'勿据此循环加规则）') if share >= 40 else '单日集中度正常'
        print(f'D 项单日集中度：最大单日 {top_day[0]} {top_day[1]} 只独立股票（{share:.0f}%）— {flag}')

    # 规则覆盖前瞻校验：区分"历史污染"vs"真退化"
    check_d_rule_coverage(db, WIN_RATE_BASELINE)


def check_d_rule_coverage(db, win_rate_baseline: float) -> None:
    """D 项规则覆盖前瞻校验：用当前 strategy.yaml 重跑所有 LIVE formal 失败样本，
    统计已被当前规则覆盖（会被 hard_veto / universe 排除）的失败笔。

    目的：D 项胜率会被"规则加入前的历史失败样本"污染（规则晚于失败样本 commit），
    单看总胜率无法区分"历史污染"vs"真退化"，导致每轮误判持续恶化、循环加规则。
    本校验把失败样本拆成两段：
      - 已被当前规则覆盖：待前瞻验证（下一个交易日 formal run 会自动确认）
      - 未被覆盖：真残留毒区清单（下一轮 D 攻击的精确目标）

    注：用 feature_snapshot_json 重跑 stock，market_regime 为事后写入值，
    故"已被覆盖"是上界估计（生产 run 时 regime 若缺失可能不触发），但作为
    "哪些失败样本已被规则覆盖"的下界信号足够稳——能拦住的就是真拦住了。
    """
    skills = _load_skills_once()
    if not skills:
        return

    rows = db.execute(text("""
        SELECT r.recommendation_id, o.outcome_label, o.executed_return,
               r.feature_snapshot_json, r.skill_breakdown_json
        FROM recommendation_outcomes o
        JOIN recommendations r ON r.recommendation_id = o.recommendation_id
        JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
        WHERE o.outcome_label IN ('success', 'failed')
          AND r.recommendation_id LIKE 'rec_%'
    """)).fetchall()
    if not rows:
        return

    blocked, new_total, wins, total = _classify_failed_coverage(rows, skills)
    new_wr = round(wins * 100 / new_total, 1) if new_total else 0.0
    delta = round(new_wr - win_rate_baseline, 1)
    tag = f'↑ 高于基线 {delta}pp' if delta >= 0 else f'⚠️ 真退化 {abs(delta)}pp'
    print(
        f'D 项规则覆盖：{blocked} 笔失败已被当前规则覆盖（待前瞻验证），'
        f'反事实胜率 {new_wr}% （n={new_total}，基线 {win_rate_baseline}%，{tag}）'
    )


def _classify_failed_coverage(rows, skills) -> tuple[int, int, int, int]:
    """统计失败样本中被当前规则覆盖（hard_veto / universe 排除）的笔数。

    纯函数（不碰 DB），便于单元测试。返回 (blocked_count, new_total, wins, total)：
      - blocked_count: 失败样本中会被当前 yaml 拦截的去重 recommendation_id 数
      - new_total: total - blocked_count（剔除被覆盖失败笔后的样本数）
      - wins: 成功笔数（不变）
      - total: 样本总数
    """
    from app.engine.strategy_runner import run_strategy

    total = len(rows)
    wins = sum(1 for r in rows if r[1] == 'success')

    blocked_rids: set = set()
    for rid, label, _ret, fsj, sbj in rows:
        if label != 'failed':
            continue
        fs = json.loads(fsj) if fsj else {}
        sb = json.loads(sbj) if sbj else []
        for s in sb:
            sid = s.get('strategy_id')
            skill = skills.get(sid)
            if not skill:
                continue
            try:
                sig = run_strategy(skill, dict(fs), [], skip_universe_check=False)
                if sig.get('hard_veto') or (sig.get('phase') == '排除'):
                    blocked_rids.add(rid)
                    break
            except Exception:
                continue

    blocked = len(blocked_rids)
    return blocked, total - blocked, wins, total


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
