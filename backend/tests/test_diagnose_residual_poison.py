"""diagnose_residual_poison 缩进/覆盖口径 + 归因顺序回归测试。

历史 bug：`if not covered: uncovered.append` 曾缩进到 for row 外，
导致诊断脚本恒报 0 残留（self_check 同期报 26 笔），D 攻击工具失效。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))
sys.path.insert(0, SCRIPTS)

from diagnose_residual_poison import (  # noqa: E402
    classify_failure_mode,
    collect_uncovered_failures,
)


class _FakeSkill:
    def __init__(self, skill_id: str):
        self.skill_id = skill_id


def _row(rid, label, ret, fs, sb, exit_reason='stop_loss_hit', max_return=-1.0):
    return (
        rid,
        label,
        ret,
        json.dumps(fs),
        json.dumps(sb),
        exit_reason,
        max_return,
    )


def test_collect_uncovered_keeps_all_failed_not_just_last():
    """缩进 bug 回归：多笔失败且均未覆盖 → 全部入 uncovered，不是只留最后一笔。"""
    skills = {'dsa': _FakeSkill('dsa')}
    rows = [
        _row('rec_a', 'failed', -5.0, {'pct': 1}, [{'strategy_id': 'dsa'}]),
        _row('rec_b', 'success', 3.0, {'pct': 2}, [{'strategy_id': 'dsa'}]),
        _row('rec_c', 'failed', -4.0, {'pct': 3}, [{'strategy_id': 'dsa'}]),
        _row('rec_d', 'failed', -3.0, {'pct': 4}, [{'strategy_id': 'dsa'}]),
    ]

    def always_pass(skill, fs, stage='market'):
        return True, 'ok'

    def never_veto(skill, fs, extras, skip_universe_check=False):
        return {'hard_veto': False, 'phase': '正常'}

    out = collect_uncovered_failures(
        rows, skills, evaluate_prefilter=always_pass, run_strategy_fn=never_veto,
    )
    rids = [x[0] for x in out]
    assert rids == ['rec_a', 'rec_c', 'rec_d'], (
        f'应收集全部 3 笔未覆盖失败，实际 {rids}——若只剩 rec_d 说明 append 缩进到 for 外'
    )


def test_collect_uncovered_marks_covered_when_prefilter_blocks():
    skills = {'dsa': _FakeSkill('dsa')}
    rows = [
        _row('rec_block', 'failed', -5.0, {'pct': 1}, [{'strategy_id': 'dsa'}]),
        _row('rec_free', 'failed', -4.0, {'pct': 2}, [{'strategy_id': 'dsa'}]),
    ]
    blocked = {'rec_block'}

    def prefilter(skill, fs, stage='market'):
        # 用 pct 区分：1 被拦、2 放行
        if fs.get('pct') == 1:
            return False, 'universe'
        return True, 'ok'

    def never_veto(skill, fs, extras, skip_universe_check=False):
        return {'hard_veto': False, 'phase': '正常'}

    out = collect_uncovered_failures(
        rows, skills, evaluate_prefilter=prefilter, run_strategy_fn=never_veto,
    )
    rids = [x[0] for x in out]
    assert rids == ['rec_free']
    assert 'rec_block' not in rids


def test_collect_uncovered_marks_covered_when_hard_veto():
    skills = {'ovn': _FakeSkill('ovn')}
    rows = [
        _row('rec_veto', 'failed', -5.0, {'pct': 1}, [{'strategy_id': 'ovn'}]),
    ]

    def always_pass(skill, fs, stage='market'):
        return True, 'ok'

    def veto(skill, fs, extras, skip_universe_check=False):
        return {'hard_veto': True, 'phase': '弱市隔夜毒区'}

    out = collect_uncovered_failures(
        rows, skills, evaluate_prefilter=always_pass, run_strategy_fn=veto,
    )
    assert out == []


def test_collect_uncovered_marks_covered_when_phase_exclude():
    skills = {'s': _FakeSkill('s')}
    rows = [
        _row('rec_ex', 'failed', -5.0, {}, [{'strategy_id': 's'}]),
    ]

    def always_pass(skill, fs, stage='market'):
        return True, 'ok'

    def exclude(skill, fs, extras, skip_universe_check=False):
        return {'hard_veto': False, 'phase': '排除'}

    out = collect_uncovered_failures(
        rows, skills, evaluate_prefilter=always_pass, run_strategy_fn=exclude,
    )
    assert out == []


def test_classify_no_follow_through_before_extension():
    """NFT 优先于 extension_chase：max<=0.5 且亏损不应被 chg20>50 抢走。"""
    mode = classify_failure_mode(
        exit_reason='stop_loss_hit',
        executed_return=-5.0,
        max_return=0.3,
        rsi_12=55,
        chg20=80,
        pos60=95,
        setup_type='多头顺势',
    )
    assert mode == 'no_follow_through'


def test_classify_gap_roundtrip():
    mode = classify_failure_mode(
        exit_reason='stop_loss_hit',
        executed_return=-5.0,
        max_return=4.2,
        rsi_12=55,
        chg20=20,
        pos60=50,
        setup_type='多头顺势',
    )
    assert mode == 'gap_roundtrip'


def test_classify_extension_when_not_nft():
    mode = classify_failure_mode(
        exit_reason='stop_loss_hit',
        executed_return=-5.0,
        max_return=1.5,  # >0.5 不是 NFT
        rsi_12=55,
        chg20=80,
        pos60=50,
        setup_type='多头顺势',
    )
    assert mode == 'extension_chase'
