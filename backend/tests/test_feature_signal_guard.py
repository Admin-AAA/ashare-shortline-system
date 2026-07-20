"""feature_signal_guard 回归测试（第 7 轮 T3.2 元护栏）。

锁定「point-biserial 单特征候选是否可信到足以为之加规则」的判定：
小样本 / 单日集中 / 时间跨度过窄 → trustworthy=False（禁止加规则）；
跨多日且不被单日主导 → trustworthy=True（仅可进入前瞻验证，非充分条件）。

背景：第 7 轮在 n≈200 LIVE 样本上连挖 3 个头部特征（holder_trade / dragon_tiger
net_buy_ratio / roe）全部被证伪为稀疏×单日伪影。本测试防止未来轮次把同类噪声
当成「高预测力因子」去加 entry/exit 规则（与 lessons #187 / #350 同构）。
"""
import pathlib
import sys

SCRIPTS_ROOT = pathlib.Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from feature_signal_guard import evaluate_feature_signal  # type: ignore[import-not-found]


def test_empty_signal_not_trustworthy():
    r = evaluate_feature_signal([])
    assert r["trustworthy"] is False
    assert r["support"] == 0
    assert "无信号正例" in r["reason"]


def test_small_sample_not_trustworthy():
    """正例 <MIN_SUPPORT（如 holder_trade>=1 仅 2 笔）→ 统计噪声，禁止加规则。"""
    dates = ["2026-07-16", "2026-07-16"]  # 本轮实证：holder_trade 正例 n=2
    r = evaluate_feature_signal(dates)
    assert r["trustworthy"] is False
    assert r["support"] == 2
    assert "统计噪声" in r["reason"]


def test_single_day_concentration_not_trustworthy():
    """正例 top 单日占比 >=40%（如 dragon_tiger failed 53% 集中 07-07）→ 单日 regime 驱动。"""
    # n=15，其中 8 笔同一天（53%）
    dates = ["2026-07-07"] * 8 + ["2026-07-06"] * 4 + ["2026-07-08"] * 3
    r = evaluate_feature_signal(dates)
    assert r["trustworthy"] is False
    assert r["top_day"] == "2026-07-07"
    assert r["top_day_ratio"] >= 0.40
    assert "单日" in r["reason"]


def test_narrow_time_span_not_trustworthy():
    """正例独立日 <3（即使 top<40%）→ 时间跨度过窄，不可信。"""
    # n=12，仅 2 个独立日，每天 6 笔（top=50% 会优先命中单日集中，改用均分跨 2 日但 top<40% 难构造，
    # 直接验证独立日<3 的拦截：2 日各 6 笔 top=50%——为独立测试 independent_days 维度，用 3 日阈值以下的 2 日）
    dates = ["2026-07-06"] * 6 + ["2026-07-07"] * 6
    r = evaluate_feature_signal(dates)
    assert r["trustworthy"] is False
    assert r["independent_days"] == 2


def test_cross_day_signal_trustworthy():
    """跨多日且 top<40%（如 success 全样本 17 日 top 28%）→ 可进入前瞻验证。"""
    # n=20 跨 5 日，每天 4 笔（top=20%）
    dates = [f"2026-07-{d:02d}" for d in (6, 7, 8, 9, 10) for _ in range(4)]
    r = evaluate_feature_signal(dates)
    assert r["trustworthy"] is True
    assert r["independent_days"] == 5
    assert r["top_day_ratio"] < 0.40
    assert "前瞻" in r["reason"] or "验证" in r["reason"]


def test_boundary_min_support():
    """边界：n 恰好 = MIN_SUPPORT 且跨多日 → 可信（支撑样本满足）。"""
    from feature_signal_guard import MIN_SUPPORT
    # MIN_SUPPORT 笔，跨 4 日均匀分布（top<40%）
    per_day = MIN_SUPPORT // 4
    remainder = MIN_SUPPORT - per_day * 4
    dates = [f"2026-07-{d:02d}" for d in (6, 7, 8, 9) for _ in range(per_day)]
    dates += ["2026-07-09"] * remainder
    r = evaluate_feature_signal(dates)
    assert r["support"] == MIN_SUPPORT
    # n 达标且 top<40% → 应可信
    assert r["trustworthy"] is True
