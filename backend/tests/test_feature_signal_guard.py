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


def test_point_biserial_positive_correlation():
    """因子值越高对应 success（y=1）越多 → r_pb > 0（资金流因子 r_pb=+0.118 同构）。"""
    from feature_signal_guard import point_biserial
    x = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
    y = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]  # 高 x=成功
    r = point_biserial(x, y)
    assert r is not None and r > 0.5  # 强正相关


def test_point_biserial_negative_correlation():
    """因子值越高对应 failed（y=0）越多 → r_pb < 0。"""
    from feature_signal_guard import point_biserial
    x = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
    y = [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]  # 高 x=失败
    r = point_biserial(x, y)
    assert r is not None and r < -0.5


def test_point_biserial_insufficient_samples_returns_none():
    """样本 <20 返回 None（POINT_BISERIAL_MIN_N 守卫）。"""
    from feature_signal_guard import point_biserial
    assert point_biserial([1, 2, 3], [0, 1, 0]) is None


def test_point_biserial_boundary_just_below_min_returns_none():
    """n=POINT_BISERIAL_MIN_N-1（19）返回 None——边界精确。

    防止未来误改 POINT_BISERIAL_MIN_N 阈值（如改成 10）而不更新调用方。
    """
    from feature_signal_guard import point_biserial, POINT_BISERIAL_MIN_N
    n = POINT_BISERIAL_MIN_N - 1
    x = list(range(1, n + 1))
    y = [0] * (n // 2) + [1] * (n - n // 2)
    assert point_biserial(x, y) is None


def test_point_biserial_boundary_at_min_returns_value():
    """n=POINT_BISERIAL_MIN_N（20）返回非 None——边界另一侧。

    与上一个测试共同锁定 MIN_N 边界：n-1→None、n→值。
    """
    from feature_signal_guard import point_biserial, POINT_BISERIAL_MIN_N
    n = POINT_BISERIAL_MIN_N
    x = list(range(1, n + 1))
    y = [0] * (n // 2) + [1] * (n - n // 2)
    assert point_biserial(x, y) is not None


def test_point_biserial_all_same_label_returns_none():
    """全 success 或全 failed（n1=0 或 n0=0）返回 None。"""
    from feature_signal_guard import point_biserial
    x = list(range(1, 25))
    assert point_biserial(x, [1] * 24) is None  # 全 success
    assert point_biserial(x, [0] * 24) is None  # 全 failed


def test_point_biserial_zero_variance_returns_none():
    """x 全相同（sd=0）返回 None——因子无变化无相关可言。"""
    from feature_signal_guard import point_biserial
    assert point_biserial([5.0] * 24, [0] * 12 + [1] * 12) is None
