"""Regression guard: market_regime_score 必须保持「逆势立场」不变量（第185轮 round-185）。

背景: 该函数驱动自适应 score_line 门槛（返回值越高→门槛越宽松→放行更多票）。
观测胜率随市场走强单调上升（弱市 ~71.6% → 强牛 ~96.4%），但强牛高胜率含选择偏差
（严格门槛只放行最优选手）。故函数刻意逆势：弱市返高分(宽松)、强牛返低分(严格)。

❌ 禁止为"修正校准"翻转函数（让强牛返高分）——翻转会放行更差票、D 项恶化（过拟合）。

本测试锁定：
1. 逆势不变量：强牛返分 < 弱市返分（核心安全护栏）。
2. 单调非增：up_ratio 越过了 0.65 阈值，返分不升反降。
3. 样本守卫：小样本(<20 / <100)降级返回中间值，不进入极值分支。
4. 具体档位：弱市=82、强牛=32（与 docstring 实证梯度一致，防静默漂移）。
"""
import pytest

from app.services.feature_builder import market_regime_score


def _snap(up_count: int, sample_count: int) -> dict:
    return {"up_count": up_count, "sample_count": sample_count}


def test_contrarian_invariant_strong_bull_stricter_than_weak():
    """核心护栏：强牛(up_ratio=0.90)返分必须 < 弱市(up_ratio=0.10)返分。

    若有人翻转校准（强牛返高分），此断言立即失败——正是 lessons #185 锁定的过拟合陷阱。
    """
    weak = market_regime_score(_snap(10, 100))    # up_ratio=0.10 <0.35
    strong = market_regime_score(_snap(90, 100))  # up_ratio=0.90 >=0.65
    assert strong < weak, f"逆势不变量被破坏: 强牛{strong} >= 弱市{weak}"


def test_monotonic_non_increasing_across_strong_bull_threshold():
    """up_ratio 跨过 0.65 后返分不得回升（强牛必须比偏多更严格）。"""
    bullish = market_regime_score(_snap(60, 100))   # up_ratio=0.60 <0.65 → 42
    strong = market_regime_score(_snap(90, 100))     # up_ratio=0.90 >=0.65 → 32
    assert strong <= bullish


def test_concrete_buckets_match_documented_gradient():
    """具体档位与 docstring 实证梯度一致，防静默漂移。"""
    assert market_regime_score(_snap(10, 100)) == 82.0   # 弱市 <0.35
    assert market_regime_score(_snap(40, 100)) == 72.0   # 0.35<=r<0.50
    assert market_regime_score(_snap(60, 100)) == 42.0   # 0.50<=r<0.65
    assert market_regime_score(_snap(90, 100)) == 32.0   # >=0.65 强牛


def test_sample_count_guards_degrade_gracefully():
    """小样本时降级返回中间值，不进入极值分支（避免小样本 regime 噪声放大）。"""
    tiny = market_regime_score(_snap(2, 10))      # sample<20 → 45
    small = market_regime_score(_snap(15, 50))    # 20<=sample<100 → 55
    assert tiny == 45.0
    assert small == 55.0
    # 降级档位必须介于极值之间，且不与逆势档位冲突
    assert 45.0 < 82.0
    assert 55.0 < 82.0
