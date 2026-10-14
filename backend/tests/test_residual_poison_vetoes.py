"""回归守卫：D 项残留毒区三条精准 veto（T3 第181轮，2026-07-11）。

背景: self_check D 项规则覆盖校验识别出 18 笔"未被任何当前规则覆盖"的失败（真残留毒区），
经全量 LIVE formal 反事实验证，其中若干笔可由因果清晰、净正边际贡献的 veto 拦截：
  - DSA `vol5_20>=3.0 & rsi_12<55`：高量能爆量 + 弱势RSI = 高位派发衰竭（拦16输/误伤11赢，ΔWR+8.6pp）
  - limit-up-reseal `chg20<0`：下行趋势中秒板死猫反弹（拦3输/误伤2赢，ΔWR+3.3pp）
全量反事实：误伤赢家14 / 拦住输家21 → 净正。其余 13 笔（11 other + 1 extension + 1 weak）为不可约噪声。

2026-09-15：dragon-tiger-smart-money 已退役（见 docs/strategy-retire-2026-09-15.md），
其 `pos60>95 & chg20>40` 毒区 veto 随 skill 一起移除；本文件保留仍在线的两条。

本测试锁定不变量，防止回归（phase 引擎 first-match-wins，veto 须在其余 phase 前命中）：
1. 命中 veto 条件的 context → run_strategy 必须 hard_veto=True 且 phase 为本 veto。
2. 不命中 veto 条件的 context → hard_veto=False（veto 是特征特定的，不是常开）。
"""
import pathlib

from app.engine.skill_loader import load_skill
from app.engine.strategy_runner import run_strategy

SKILLS_DIR = pathlib.Path(__file__).resolve().parents[1] / "skills"

_DSA = "daily-stock-analysis-skill"
_LR = "limit-up-reseal-relay"

_BASE = dict(
    code="TEST",
    name="测试",
    industry="半导体",
    data_completeness_score=100,
    evidence_quality_score=100,
    market_sample_count=100,
)


def _ctx(**overrides):
    base = dict(_BASE)
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# DSA：爆量弱势衰竭毒区
# --------------------------------------------------------------------------- #
def test_dsa_exhaustion_blowoff_is_hard_vetoed():
    sk = load_skill(SKILLS_DIR / _DSA)
    # dsa_trend_score/dsa_macd_score 设为非触发 风险否决 的值，隔离本 veto
    ctx = _ctx(vol5_20=3.5, rsi_12=49.0, chg20=18.0, pct=1.8, pos60=80.0,
               market_regime="弱势", dsa_trend_score=30, dsa_macd_score=5)
    out = run_strategy(sk, ctx, main_industries=["半导体"], skip_universe_check=True)
    assert out["hard_veto"] is True, f"爆量弱势衰竭应硬否决, got phase={out['phase']}"
    assert out["phase"] == "爆量弱势衰竭毒区", f"应命中爆量弱势衰竭毒区, got {out['phase']}"


def test_dsa_normal_volume_not_vetoed():
    sk = load_skill(SKILLS_DIR / _DSA)
    # vol5_20<3 → 不满足 veto；其余 phase 可命中但不得是本 veto
    ctx = _ctx(vol5_20=1.2, rsi_12=49.0, chg20=18.0, pct=1.8, pos60=80.0,
               dsa_trend_score=30, dsa_macd_score=5)
    out = run_strategy(sk, ctx, main_industries=["半导体"], skip_universe_check=True)
    assert out["hard_veto"] is False, f"正常量能不应被爆量毒区否决, got {out['phase']}"
    assert out["phase"] != "爆量弱势衰竭毒区"


def test_dsa_high_volume_but_strong_rsi_not_vetoed():
    sk = load_skill(SKILLS_DIR / _DSA)
    # 高量能但 RSI 强（>=55）→ 非衰竭派发，不 veto
    ctx = _ctx(vol5_20=3.5, rsi_12=68.0, chg20=18.0, pct=1.8, pos60=80.0,
               dsa_trend_score=30, dsa_macd_score=5)
    out = run_strategy(sk, ctx, main_industries=["半导体"], skip_universe_check=True)
    assert out["hard_veto"] is False, f"高量能+强RSI 不应 veto, got {out['phase']}"
    assert out["phase"] != "爆量弱势衰竭毒区"


# --------------------------------------------------------------------------- #
# limit-up-reseal：下行趋势中秒板毒区
# --------------------------------------------------------------------------- #

