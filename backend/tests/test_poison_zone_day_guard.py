"""锁定 poison_zone_day_guard 单日集中度护栏判定阈值（防每轮对单日毒区重复加规则）。"""
from __future__ import annotations

import sys
from pathlib import Path

# scripts/ 与 backend/ 同级，取当前 worktree 根的 scripts 目录
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from poison_zone_day_guard import (  # noqa: E402
    single_day_concentration,
    report_poison_zone_guard,
    subcluster_day_concentration,
)


def test_empty_is_driven() -> None:
    r = single_day_concentration([])
    assert r["single_day_driven"] is True
    assert r["n"] == 0


def test_single_day_is_driven() -> None:
    # 07-16 毒区形态：全部集中一个交易日 → 禁止加规则
    r = single_day_concentration(["2026-07-16"] * 28)
    assert r["single_day_driven"] is True
    assert r["independent_days"] == 1
    assert r["top_day_ratio"] == 1.0
    assert "独立交易日仅 1 天" in r["reason"]


def test_two_days_below_min_is_driven() -> None:
    r = single_day_concentration(["2026-07-06"] * 5 + ["2026-07-07"] * 3)
    assert r["single_day_driven"] is True
    assert r["independent_days"] == 2


def test_one_day_dominates_half_is_driven() -> None:
    # 跨 4 天但单日占 50% → 仍单日主导
    dates = (["2026-07-16"] * 10 + ["2026-07-06"] * 4
             + ["2026-07-07"] * 3 + ["2026-07-08"] * 3)
    r = single_day_concentration(dates)
    assert r["single_day_driven"] is True
    assert r["top_day"] == "2026-07-16"
    assert r["top_day_ratio"] == 0.5


def test_dispersed_across_days_not_driven() -> None:
    # 跨 5 天、最大单日占比 <50% → 可考虑 entry 规则（仍需分段胜率验证）
    dates = (["2026-07-06"] * 3 + ["2026-07-07"] * 3 + ["2026-07-08"] * 2
             + ["2026-07-09"] * 1 + ["2026-07-10"] * 1)
    r = single_day_concentration(dates)
    assert r["single_day_driven"] is False
    assert r["independent_days"] == 5
    assert r["top_day_ratio"] == 0.3


def test_report_format() -> None:
    line = report_poison_zone_guard("no_follow_through", ["2026-07-16"] * 28)
    assert "no_follow_through" in line
    assert "禁止加 entry 规则" in line


def test_subcluster_detects_hidden_single_day() -> None:
    # no_follow_through 形态：模式级跨 5 天（绿灯），但 high_vol 子簇 8/9 集中 07-16
    items = (
        [("high_vol", "2026-07-16")] * 8 + [("high_vol", "2026-07-10")] * 1
        + [("low_vol", "2026-07-06")] * 4 + [("low_vol", "2026-07-07")] * 4
        + [("low_vol", "2026-05-29")] * 1
    )
    worst = subcluster_day_concentration(items)
    assert worst is not None
    assert worst["cluster_key"] == "high_vol"
    assert worst["top_day"] == "2026-07-16"
    assert worst["top_day_ratio"] >= 0.5


def test_subcluster_none_when_dispersed() -> None:
    # 各子簇都跨多日、无单日主导 → None（不拦）
    items = (
        [("a", "2026-07-06")] * 2 + [("a", "2026-07-07")] * 2 + [("a", "2026-07-08")] * 2
        + [("b", "2026-07-06")] * 1 + [("b", "2026-07-07")] * 1 + [("b", "2026-07-08")] * 1
    )
    assert subcluster_day_concentration(items) is None


def test_subcluster_skips_small_clusters() -> None:
    # 子簇样本 < min_subcluster 不判定（避免小样本误报）
    items = [("tiny", "2026-07-16")] * 2
    assert subcluster_day_concentration(items, min_subcluster=3) is None
