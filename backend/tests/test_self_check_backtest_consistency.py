"""T2 backtest 一致性诊断纯函数测试（_backtest_consistency_stats）。

锁定 P0 backtest vs LIVE formal 口径差异的可观测性：backtest 是单日 signal 回测
（常含毒区日），LIVE formal 是全市场多日 + 规则覆盖 + LLM 审查。backtest 低胜率
非策略退化——防「backtest 27.9% vs LIVE 71.7% 误判策略差」。
"""
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]


def test_stats_groups_by_strategy():
    trades = [
        ("daily-stock-analysis-skill", "success", 5.0),
        ("daily-stock-analysis-skill", "failed", -5.69),
        ("synthetic-secondary-skill", "success", 3.0),
    ]
    stats = self_check._backtest_consistency_stats(trades)
    assert set(stats.keys()) == {"daily-stock-analysis-skill", "synthetic-secondary-skill"}
    # daily-stock: 1 win 1 loss → WR=50%, avg=(5-5.69)/2=-0.345, PL=5/5.69
    assert stats["daily-stock-analysis-skill"]["n"] == 2
    assert stats["daily-stock-analysis-skill"]["wr"] == 50.0
    assert stats["daily-stock-analysis-skill"]["avg"] == round((5.0 + (-5.69)) / 2, 2)
    assert stats["daily-stock-analysis-skill"]["pl"] == round(5.0 / 5.69, 2)


def test_stats_low_wr_not_flagged_as_degradation():
    """backtest 低胜率（27.9%）是单日 signal 口径，非退化。函数只算统计不判退化。"""
    trades = [("daily-stock-analysis-skill", "failed", -5.0)] * 7 + [("daily-stock-analysis-skill", "success", 3.0)] * 3
    stats = self_check._backtest_consistency_stats(trades)
    # 3/10 = 30% —— 只报统计，不标退化（口径差异由 check_backtest_consistency 文字说明）
    assert stats["daily-stock-analysis-skill"]["wr"] == 30.0
    assert stats["daily-stock-analysis-skill"]["n"] == 10


def test_stats_handles_none_strategy():
    items = [("daily-stock", "success", 5.0), (None, "success", 3.0)]
    stats = self_check._backtest_consistency_stats(items)
    assert set(stats.keys()) == {"daily-stock"}


def test_stats_handles_none_net():
    items = [("daily-stock", "failed", None)]
    stats = self_check._backtest_consistency_stats(items)
    assert stats["daily-stock"]["n"] == 1
    assert stats["daily-stock"]["avg"] == 0.0
    assert stats["daily-stock"]["wr"] == 0.0


def test_stats_pl_zero_when_all_wins():
    items = [("daily-stock", "success", 5.0), ("daily-stock", "success", 3.0)]
    stats = self_check._backtest_consistency_stats(items)
    assert stats["daily-stock"]["pl"] == 0.0  # 无亏损，PL=0


def test_stats_empty_returns_empty():
    assert self_check._backtest_consistency_stats([]) == {}


def test_stats_case_insensitive_outcome_label():
    """outcome_label 大小写不敏感（success/SUCCESS/Success 都算胜）。"""
    items = [("daily-stock", "SUCCESS", 5.0), ("daily-stock", "Success", 3.0)]
    stats = self_check._backtest_consistency_stats(items)
    assert stats["daily-stock"]["wr"] == 100.0
