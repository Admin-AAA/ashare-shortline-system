"""failure_attribution.py 纯函数单测（不依赖 DB）。"""
from scripts.failure_attribution import (
    point_biserial,
    bucket_economics,
    threshold_simulation,
    exit_reason_distribution,
    recommend,
)


def test_point_biserial_perfect_positive():
    # 40 样本，50/50 二分切在中位数；点二列相关约 0.87（biserial 关系，非 1.0）
    xs = [float(i) for i in range(40)]
    yb = [0] * 20 + [1] * 20
    r, n = point_biserial(xs, yb)
    assert n == 40
    assert r is not None and r > 0.8


def test_point_biserial_requires_min_samples():
    # < 30 样本返回 None（统计稳定性门槛）
    r, n = point_biserial([1.0, 2.0, 3.0], [0, 1, 0])
    assert r is None and n == 3


def test_point_biserial_low_samples_returns_none():
    r, n = point_biserial([1.0, 2.0], [0, 1])
    assert r is None and n == 2


def test_point_biserial_ignores_none():
    xs = [1.0, None, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    yb = [0, 1, 0, 0, 0, 1, 1, 1, 1, 1]
    r, n = point_biserial(xs, yb)
    assert n == 9  # None 被剔除


def test_bucket_economics_aggregates_correctly():
    scores = [57.0, 58.0, 62.0, 63.0, 68.0, 69.0]
    returns = [2.0, -1.0, 3.0, -0.5, 4.0, 1.0]
    econ = bucket_economics(scores, returns, bucket=5)
    by_bucket = {e["bucket"]: e for e in econ}
    # 55-59: 2 笔, 净收益 (2-1)/2 = 0.5
    assert by_bucket["55-59"]["n"] == 2
    assert by_bucket["55-59"]["mean_ret"] == 0.5
    assert by_bucket["55-59"]["loss_rate"] == 50.0
    # 60-64: 2 笔
    assert by_bucket["60-64"]["n"] == 2
    # 65-69: 2 笔
    assert by_bucket["65-69"]["n"] == 2


def test_threshold_simulation_keeps_higher_only():
    scores = [57.0, 58.0, 62.0, 68.0]
    returns = [2.0, 1.0, 3.0, 4.0]
    sim = threshold_simulation(scores, returns, [60.0])
    assert sim[60.0]["kept_n"] == 2  # 62, 68
    assert sim[60.0]["dropped_n"] == 2  # 57, 58
    # 全量均值 (2+1+3+4)/4 = 2.5
    assert sim[60.0]["mean_ret"] == 3.5  # (3+4)/2
    assert sim[60.0]["delta_vs_all"] == 1.0


def test_exit_reason_distribution_counts():
    dist = exit_reason_distribution(["stop_loss_hit", "trailing_stop_hit",
                                     "stop_loss_hit", None])
    assert dist == {"stop_loss_hit": 2, "trailing_stop_hit": 1}


def test_recommend_warns_when_threshold_hurts():
    # 模拟：抬高门槛后净收益下降
    sim = {
        58.0: {"kept_n": 100, "dropped_n": 0, "mean_ret": 3.3, "delta_vs_all": 0.0},
        60.0: {"kept_n": 88, "dropped_n": 12, "mean_ret": 3.27, "delta_vs_all": -0.03},
    }
    rec = recommend(sim)
    assert "不建议" in rec
