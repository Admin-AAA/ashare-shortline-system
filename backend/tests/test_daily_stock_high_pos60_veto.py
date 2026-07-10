#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T3 D 项残留毒区攻击（第172轮）回归测试。

背景：D 项真残留毒区 26 笔中，extension_chase 子集（pos60>90）是唯一的
「每股票因子可解」毒区——daily-stock-analysis-skill（通用分析技能，非打板/接力）
追 60 日高位净负：LIVE 去重样本 n=66 中 pos60>90 仅 7 笔但 WR=57.1% avg=-0.20%，
显著低于其余 59 笔 WR=71.2% avg=+1.56%。

关键约束（防过拟合护栏）：全样本 pos60>90 反而 WR=86.4%（涨停/龙虎榜等动量
策略在高位赢）。因此该 hard_veto 必须**仅限 daily-stock-analysis-skill**，绝不能
全局化——否则会误伤动量策略的 alpha。本测试锁定这一点。

规则：daily-stock-analysis-skill 新增 phase「接近60日高位追高毒区」when pos60>90
hard_veto=true（与本技能既有 pos60<50 极端位否决同档）。
"""
import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engine.skill_loader import load_skills as _load_skills
from app.engine.strategy_runner import run_strategy

SKILLS_DIR = pathlib.Path(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'skills'))


def _skills():
    out = {}
    for s in _load_skills(SKILLS_DIR):
        out[s.skill_id] = s
    return out


def _dsa_fs(**over):
    base = {
        'pos60': 80.0, 'dsa_trend_score': 30, 'dsa_bias_score': 8,
        'dsa_macd_score': 8, 'pct': 3.0, 'rsi_12': 50, 'news_risk_count': 0,
        'market_regime': '活跃', 'chg20': 20, 'dist60': 20,
    }
    base.update(over)
    return base


@pytest.fixture(scope='module')
def skills():
    return _skills()


def test_dsa_vetoes_pos60_above_90(skills):
    dsa = skills['daily-stock-analysis-skill']
    sig = run_strategy(dsa, _dsa_fs(pos60=92.98), [], skip_universe_check=True)
    assert sig['hard_veto'] is True
    assert sig['phase'] == '接近60日高位追高毒区'
    assert any('接近60日高位' in f for f in sig['risk_flags'])


def test_dsa_allows_pos60_below_90(skills):
    dsa = skills['daily-stock-analysis-skill']
    sig = run_strategy(dsa, _dsa_fs(pos60=80.0), [], skip_universe_check=True)
    assert sig['hard_veto'] is False
    # 80 仍属正常趋势跟踪区间，不应被该 phase 误伤
    assert sig['phase'] != '接近60日高位追高毒区'


def test_dsa_exact_boundary_90_not_vetoed(skills):
    """阈值严格 >90，pos60==90 不触发（避免边界抖动误伤）。"""
    dsa = skills['daily-stock-analysis-skill']
    sig = run_strategy(dsa, _dsa_fs(pos60=90.0), [], skip_universe_check=True)
    assert sig['hard_veto'] is False


def test_momentum_skill_must_not_veto_pos60_above_90(skills):
    """防过拟合护栏：动量/打板策略在高位赢（全样本 pos60>90 WR=86.4%），
    绝不能被本规则波及。锁定 chen-xiaoqun-skill 不在 pos60>90 时 hard_veto。"""
    mom = skills.get('chen-xiaoqun-skill')
    if mom is None:
        pytest.skip('chen-xiaoqun-skill 未加载')
    sig = run_strategy(mom, _dsa_fs(pos60=95.0), [], skip_universe_check=True)
    assert sig['hard_veto'] is False, (
        'pos60>90 全局化会误伤动量策略 alpha；该否决必须仅限 daily-stock-analysis-skill'
    )
