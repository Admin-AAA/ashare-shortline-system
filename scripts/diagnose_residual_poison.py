#!/usr/bin/env python3
"""诊断 D 项真残留毒区（未被任何当前规则覆盖的失败笔）。

复刻 self_check.py 的 _collect_uncovered_failures 口径（evaluate_skill_prefilter
universe+prefilter.market.include/include_any/exclude + run_strategy hard_veto/排除phase），
但额外 dump 每个残留失败笔的关键 feature_snapshot 字段，供下一轮 D 攻击找通用规则。

用法（在 worktree 内）：
    cd backend && source .venv/bin/activate && python3 ../scripts/diagnose_residual_poison.py
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from typing import Any, Callable

BACKEND_DIR = os.path.join(os.path.dirname(__file__), '..', 'backend')
sys.path.insert(0, os.path.abspath(BACKEND_DIR))

from app.db.session import SessionLocal
from sqlalchemy import text

SKILLS_DIR = os.path.join(os.path.dirname(__file__), '..', 'backend', 'skills')

# 内联镜像 self_check._classify_failure_mode_inline / failure_attribution
# （顺序必须一致：NFT 在 extension_chase 前，否则 max<=0.5 失败会被 chg20/pos60 误标）
WEAK_SETUPS = {"分歧回封接力", "放量突破", "阶段高点蓄势观察"}

FIELDS = [
    'code', 'name', 'pct', 'chg20', 'chg5', 'pos60', 'pos20', 'rsi_12', 'rsi',
    'amount_yi', 'turnover_rate', 'free_mv', 'total_mv', 'is_main_theme',
    'setup_type', 'market_regime', 'twenty_cm', 'industry', 'board',
    'vol5_20', 'circ_mv', 'float_mv',
]


def classify_failure_mode(
    exit_reason,
    executed_return,
    max_return,
    rsi_12,
    chg20,
    pos60,
    setup_type,
) -> str:
    """与 self_check._classify_failure_mode_inline 同序，防归因漂移。"""
    if exit_reason == "stop_loss_gap":
        return "tail_gap"
    if (
        exit_reason in ("trailing_stop_hit", "stop_loss_hit")
        and (max_return is not None and max_return > 3.0)
        and (executed_return is not None and executed_return < 0.0)
    ):
        return "gap_roundtrip"
    if setup_type in WEAK_SETUPS:
        return "bad_setup"
    if rsi_12 is not None and rsi_12 < 45:
        return "weak_stock"
    if (
        max_return is not None
        and executed_return is not None
        and max_return <= 0.5
        and executed_return < 0.0
    ):
        return "no_follow_through"
    if (chg20 is not None and chg20 > 50) or (pos60 is not None and pos60 > 90):
        return "extension_chase"
    return "other"


def collect_uncovered_failures(
    rows: list,
    skills: dict,
    *,
    evaluate_prefilter: Callable | None = None,
    run_strategy_fn: Callable | None = None,
) -> list[tuple]:
    """采集失败样本中未被当前规则覆盖的笔。

    纯函数（skills/eval/run 可注入），便于单测锁定缩进/覆盖口径。
    返回 list[(rid, fs, exit_reason, max_return, ret, strategy_ids)]。

    历史 bug（第 11 轮）：`if not covered: uncovered.append(...)` 曾缩进到 for 外，
    导致循环只可能留下最后一笔失败 → 诊断脚本恒报 0 残留，掩盖 self_check 的 26 笔。
    """
    if evaluate_prefilter is None or run_strategy_fn is None:
        from app.engine.strategy_runner import run_strategy, evaluate_skill_prefilter
        if evaluate_prefilter is None:
            evaluate_prefilter = evaluate_skill_prefilter
        if run_strategy_fn is None:
            run_strategy_fn = run_strategy

    uncovered: list[tuple] = []
    for row in rows:
        rid, label, ret, fsj, sbj = row[0], row[1], row[2], row[3], row[4]
        exit_reason = row[5] if len(row) > 5 else None
        max_return = row[6] if len(row) > 6 else None
        if label != 'failed':
            continue
        fs = json.loads(fsj) if isinstance(fsj, str) else (fsj or {})
        sb = json.loads(sbj) if isinstance(sbj, str) else (sbj or [])
        if not isinstance(fs, dict):
            fs = {}
        if not isinstance(sb, list):
            sb = []
        covered = False
        # 路 3：与 self_check 对齐——震荡 soft floor 历史票算已覆盖
        meta = fs.get('score_line_meta') if isinstance(fs, dict) else None
        if isinstance(meta, dict) and meta.get('dynamic_soft_floor_applied') and meta.get('market_base') == '震荡':
            covered = True
        for s in sb:
            if covered:
                break
            sid = s.get('strategy_id') if isinstance(s, dict) else None
            skill = skills.get(sid) if sid else None
            if not skill:
                continue
            try:
                prefilter_pass, _reason = evaluate_prefilter(
                    skill, dict(fs), stage='market'
                )
                sig = run_strategy_fn(skill, dict(fs), [], skip_universe_check=False)
                if (not prefilter_pass) or sig.get('hard_veto') or (sig.get('phase') == '排除'):
                    covered = True
                    break
            except Exception:
                continue
        # 关键：此 append 必须在 for row 内、for s 外——否则只剩最后一笔
        if not covered:
            uncovered.append((
                rid,
                fs,
                exit_reason,
                max_return,
                ret,
                [s.get('strategy_id') for s in sb if isinstance(s, dict)],
            ))
    return uncovered


def main() -> None:
    from app.engine.skill_loader import load_skills
    from pathlib import Path

    skills = {s.skill_id: s for s in load_skills(Path(SKILLS_DIR))}

    db = SessionLocal()
    rows = db.execute(text("""
        SELECT r.recommendation_id, o.outcome_label, o.executed_return,
               r.feature_snapshot_json, r.skill_breakdown_json,
               o.exit_reason, o.max_return
        FROM recommendation_outcomes o
        JOIN recommendations r ON r.recommendation_id = o.recommendation_id
        JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
        WHERE o.outcome_label IN ('success', 'failed')
          AND r.recommendation_id LIKE 'rec_%'
    """)).fetchall()
    db.close()

    uncovered = collect_uncovered_failures(rows, skills)

    print(f"真残留毒区总数: {len(uncovered)}")
    modes: Counter = Counter()
    for rid, fs, exit_reason, max_return, ret, _sids in uncovered:
        mode = classify_failure_mode(
            exit_reason, ret, max_return, fs.get('rsi_12'),
            fs.get('chg20'), fs.get('pos60'), fs.get('setup_type'),
        )
        modes[mode] += 1
    print("模式分布:", dict(modes))

    print("\n=== 逐笔明细 ===")
    strat_counter: Counter = Counter()
    for i, (rid, fs, exit_reason, max_return, ret, sids) in enumerate(uncovered, 1):
        mode = classify_failure_mode(
            exit_reason, ret, max_return, fs.get('rsi_12'),
            fs.get('chg20'), fs.get('pos60'), fs.get('setup_type'),
        )
        for sid in sids:
            if sid:
                strat_counter[sid] += 1
        line = []
        for f in FIELDS:
            v = fs.get(f)
            if v is not None:
                line.append(f"{f}={v}")
        tag = " / ".join(s for s in sids if s) if sids else "?"
        print(
            f"\n[{i}] {rid} | strategies={tag} | mode={mode} "
            f"exit={exit_reason} ret={ret} maxret={max_return}"
        )
        print("   " + " ".join(line))

    print("\n=== 残留毒区按策略分布（精确攻击目标）===")
    for sid, cnt in strat_counter.most_common():
        print(f"  {sid}: {cnt} 笔")


if __name__ == '__main__':
    main()
