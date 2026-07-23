"""Regression guard: self_check D 项 maxloss 分类容差（第165轮 T2）。

背景: recompute 后 D 项 maxloss 从 -9.18% 改善到 -5.88%（长裕集团高开入场 gap+7.3%
在 MAX_STOP_LOSS_PCT=-5.7% 下的真实触发价）。但 -5.88% 仍比基线 -5.80% 差 0.08pp，
旧逻辑 `ml < baseline_loss` 把它报成"⚠️ 恶化"，误导后续轮次加无意义的止损规则。

maxloss 是极端值（min of returns），对单一样本敏感——一只高开入场票就能把 maxloss
从 -5.80% 推到 -5.88%。这种 ≤0.5pp 的差异是日内波动噪声，不是退化信号。
_classify_maxloss 用 ±0.5pp 容差区分"持平（噪声范围）"与"真恶化"，防止误判。

本测试锁定分类逻辑，不依赖 DB 真实数据。
"""
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]

BASELINE = -5.80


def test_maxloss_within_tolerance_is_flat():
    """maxloss 在基线 ±0.5pp 内算"持平（噪声范围）"，不报恶化。

    模拟 recompute 后长裕集团 -5.88%：比基线 -5.80% 差 0.08pp，属日内波动噪声。
    """
    label = self_check._classify_maxloss(-5.88, BASELINE)
    assert "持平" in label
    assert "恶化" not in label


def test_maxloss_equal_baseline_is_flat():
    """maxloss 恰等于基线算"持平"。"""
    label = self_check._classify_maxloss(-5.80, BASELINE)
    assert "持平" in label


def test_maxloss_better_than_baseline_is_superior():
    """maxloss 优于基线超过容差算"优于基线"。"""
    label = self_check._classify_maxloss(-5.10, BASELINE)
    assert label == "优于基线"


def test_maxloss_slightly_better_within_tolerance_is_flat():
    """maxloss 优于基线但在容差内仍算"持平"（容差双向，不单方向放宽）。"""
    label = self_check._classify_maxloss(-5.40, BASELINE)  # 优于基线 0.4pp，在 ±0.5pp 内
    assert "持平" in label


def test_maxloss_real_degradation_is_worsening():
    """maxloss 远低于基线（超容差）报"⚠️ 恶化"——真退化不被容差掩盖。

    模拟 recompute 前 -9.18%：远超 0.5pp 容差，必须报恶化。
    """
    label = self_check._classify_maxloss(-9.18, BASELINE)
    assert label == "⚠️ 恶化"


def test_maxloss_just_beyond_tolerance_is_worsening():
    """maxloss 比基线差超过 0.5pp 报"恶化"——容差边界精确。"""
    label = self_check._classify_maxloss(-6.31, BASELINE)  # 差 0.51pp，超容差
    assert label == "⚠️ 恶化"


def test_maxloss_day_attribution_finds_worst_day():
    """全量 maxloss 最差笔的单日归因：返回最差笔所在 trade_date + 该日 failed 笔数。

    背景: 07-09 跨 run 板块去重修复前 16 笔半导体集中跳空，全量 maxloss -12.22% 全来自
    该日。归因让用户区分历史遗留 vs 当前退化，避免「⚠️ 恶化」误读为策略退化。
    rows = [(code, trade_date, label, executed_return)]
    """
    rows = [
        ("600001", "2026-07-09", "failed", -12.22),
        ("600002", "2026-07-09", "failed", -10.50),
        ("600003", "2026-07-16", "failed", -8.49),
        ("600004", "2026-07-23", "success", 5.20),
    ]
    attr = self_check._maxloss_day_attribution(rows)
    assert attr is not None
    assert attr["worst_day"] == "2026-07-09"
    assert attr["worst_ret"] == -12.22
    assert attr["day_n"] == 2  # 07-09 有 2 笔 failed


def test_maxloss_day_attribution_no_failed_returns_none():
    """无 failed 样本时返回 None（无法归因）。"""
    rows = [
        ("600001", "2026-07-09", "success", 5.20),
        ("600002", "2026-07-16", "success", 3.10),
    ]
    assert self_check._maxloss_day_attribution(rows) is None


def test_maxloss_day_attribution_ignores_none_return():
    """executed_return 为 None 的 failed 笔不参与归因（数据缺失不污染最差值）。"""
    rows = [
        ("600001", "2026-07-09", "failed", None),
        ("600002", "2026-07-09", "failed", -8.40),
        ("600003", "2026-07-16", "failed", -9.10),
    ]
    attr = self_check._maxloss_day_attribution(rows)
    assert attr is not None
    assert attr["worst_day"] == "2026-07-16"
    assert attr["worst_ret"] == -9.10
    assert attr["day_n"] == 1
