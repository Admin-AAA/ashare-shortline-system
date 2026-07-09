"""Regression guard: overnight-holding-method 弱市硬否决 + 实证权重 (T3.6 第160轮).

背景: 2026-07 发现 overnight 全量 formal WR=35%(n=20) 却仍持满权 weight=1.0，且 14/15 历史推荐
regime=弱势——弱市隔夜追高毒区。strategy.yaml 虽有 `弱市隔夜毒区` phase (market_regime=='弱势'
→ hard_veto)，但曾因后端未重载 / context 缺 market_regime 而静默不生效，弱市亏损票照常推荐。

本测试锁定两条不变量，防止回归:
1. 弱市 context → run_strategy 必须 hard_veto=True (phase=弱市隔夜毒区)，而非落到 尾盘观察。
2. 非弱市 context → hard_veto=False (veto 是 regime 特定的，不是常开)。
3. strategy.yaml 顶层 weight 必须注入 signal.weight (实证加权: 低胜率策略降权)。
"""
import pathlib

from app.engine.skill_loader import load_skill
from app.engine.strategy_runner import run_strategy


SKILLS_DIR = pathlib.Path(__file__).resolve().parents[1] / "skills"
SKILL_ID = "overnight-holding-method"


def _load():
    return load_skill(SKILLS_DIR / SKILL_ID)


def _weak_ctx(**overrides):
    """通过 universe (pct∈[3,5], turnover∈[5,10], dist20>=0) 且 regime=弱势 的 context。"""
    base = {
        "code": "603268",
        "name": "测试",
        "industry": "半导体",
        "data_completeness_score": 100,
        "evidence_quality_score": 100,
        "market_sample_count": 100,
        "pct": 4.0,
        "turnover_rate": 7.0,
        "dist20": 5.0,  # universe.dist20>=0 通过
        "market_regime": "弱势",
    }
    base.update(overrides)
    return base


def test_weak_market_overnight_is_hard_vetoed():
    """弱市隔夜追高毒区必须硬否决——这是 35% WR 的根源修复，绝不能静默失效。"""
    sk = _load()
    out = run_strategy(sk, _weak_ctx(), main_industries=["半导体"], skip_universe_check=True)
    assert out["hard_veto"] is True, f"弱市隔夜应被硬否决, got phase={out['phase']}"
    assert out["phase"] == "弱市隔夜毒区", f"应命中弱市毒区 phase, got {out['phase']}"


def test_non_weak_market_overnight_not_vetoed():
    """非弱市不应被该 phase 否决——veto 是 regime 特定的, 不是常开。"""
    sk = _load()
    out = run_strategy(
        sk, _weak_ctx(market_regime="强势"), main_industries=["半导体"], skip_universe_check=True
    )
    assert out["hard_veto"] is False, f"非弱市不应被弱市 phase 否决, got {out['phase']}"
    assert out["phase"] != "弱市隔夜毒区"


def test_overnight_weight_injected_and_downweighted():
    """低胜率策略必须按实证加权降权 (35% WR → weight=0.35 地板, 不再满权 1.0)。

    第157轮先降到 0.5 中间档待 post-veto 复核；本轮 post-veto 样本 WR 不升反降至
    25%(n=12)，连续 2 轮验证 WR<45% 且归因无解 → 按第157轮计划降到 0.35 最低权重档。
    """
    sk = _load()
    assert sk.contract.get("weight") == 0.35, f"overnight weight 应为 0.35 地板, got {sk.contract.get('weight')}"
    out = run_strategy(sk, _weak_ctx(), main_industries=["半导体"], skip_universe_check=True)
    assert out["weight"] == 0.35
