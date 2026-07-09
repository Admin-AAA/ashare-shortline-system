"""Regression guard: self_check D 项规则覆盖前瞻校验 (第161轮 T3).

背景: D 项胜率被"规则加入前的历史失败样本"污染——规则晚于失败样本 commit，
单看总胜率无法区分"历史污染"vs"真退化"，导致每轮 self-improve 误判持续恶化、
循环加规则过拟合。_classify_failed_coverage 用当前 strategy.yaml 重跑失败样本，
统计被 hard_veto / universe 排除的笔数，给出反事实胜率。

本测试锁定纯统计逻辑（不依赖 DB 真实数据），用 overnight 构造样本验证：
1. 弱市失败笔若被当前 yaml hard_veto（弱市隔夜毒区）→ 计入 blocked，反事实胜率上升。
2. 非弱市失败笔未被 veto → 不计入 blocked。
3. 成功样本不计入 blocked（反事实只剔除失败笔）。
"""
import json
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.engine.skill_loader import load_skills

import self_check  # type: ignore[import-not-found]

SKILLS_DIR = BACKEND_ROOT / "skills"


def _skills():
    return {s.skill_id: s for s in load_skills(SKILLS_DIR)}


def _overnight_ctx(market_regime: str) -> dict:
    """overnight universe 放行 + regime 可控的 context。

    pct∈[3,5] turnover∈[5,10] dist20>=0 满足 universe.include；
    limit_up_days20>=1 满足 prefilter.include。
    regime=弱势 → 命中 弱市隔夜毒区 hard_veto；regime=强势 → 落 尾盘观察 不 veto。
    """
    return {
        "code": "603268", "name": "松发股份", "industry": "通用",
        "pct": 4.0, "turnover_rate": 6.0, "dist20": 5.0,
        "limit_up_days20": 1, "twenty_cm_risk_gate": "pass",
        "market_regime": market_regime, "market_sample_count": 4000,
        "capital_resonance_score": 50, "sector_ladder_completeness_score": 50,
        "limit_up_overheat_score": 30,
    }


def _row(rid: str, label: str, ret: float, fs: dict, skill_id: str):
    sb = [{"strategy_id": skill_id}]
    return (rid, label, ret, json.dumps(fs), json.dumps(sb))


def test_blocked_failure_raises_counterfactual_winrate():
    """弱市失败笔被 veto 覆盖 → blocked=1，反事实胜率 > 原始胜率。"""
    rows = [
        _row("rec_test_001", "failed", -5.69, _overnight_ctx("弱势"), "overnight-holding-method"),
        _row("rec_test_002", "failed", -5.72, _overnight_ctx("强势"), "overnight-holding-method"),
        _row("rec_test_003", "success", 6.39, _overnight_ctx("强势"), "overnight-holding-method"),
    ]
    blocked, new_total, wins, total = self_check._classify_failed_coverage(rows, _skills())
    assert total == 3
    assert wins == 1
    assert blocked == 1  # 仅 弱市那笔被 veto 覆盖
    assert new_total == 2  # 剔除 1 笔被覆盖失败后剩 2 笔
    # 反事实胜率 = 1 win / 2 = 50%；原始 = 1/3 = 33.3% → 反事实更高
    assert round(wins * 100 / new_total, 1) == 50.0


def test_uncovered_failure_not_blocked():
    """非弱市失败笔未被 veto → 不计入 blocked。"""
    rows = [_row("rec_test_002", "failed", -5.72, _overnight_ctx("强势"), "overnight-holding-method")]
    blocked, new_total, wins, total = self_check._classify_failed_coverage(rows, _skills())
    assert blocked == 0
    assert total == 1
    assert new_total == 1  # 没有被覆盖的，总数不变
    assert wins == 0


def test_success_never_blocked():
    """成功样本永远不计入 blocked（反事实只剔除失败笔）。"""
    rows = [
        _row("rec_a", "success", 6.39, _overnight_ctx("弱势"), "overnight-holding-method"),
        _row("rec_b", "success", 8.0, _overnight_ctx("强势"), "overnight-holding-method"),
    ]
    blocked, new_total, wins, total = self_check._classify_failed_coverage(rows, _skills())
    assert blocked == 0
    assert wins == 2
    assert new_total == 2


def test_unknown_skill_does_not_crash():
    """skill_breakdown 里的 strategy_id 在 skills 字典找不到时不应崩溃（静默跳过）。"""
    rows = [_row("rec_x", "failed", -5.0, _overnight_ctx("弱势"), "nonexistent-skill")]
    blocked, new_total, wins, total = self_check._classify_failed_coverage(rows, _skills())
    assert blocked == 0  # 未知策略无法重跑，不算被覆盖
    assert total == 1
