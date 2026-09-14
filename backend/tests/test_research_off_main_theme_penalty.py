"""锁定 canonical 三窗测量里的主线重建与"只补缺失字段"不变量。

背景（2026-09-14，L-112）：W2 前瞻 / W3 第三窗的 canonical `market_snapshot`
只含涨跌停计数，没有 `main_industries`，导致任何依赖"当日主线"的候选在这两窗
静默失效（Δ 恒为 0）。重建时用的公式必须与生产同源
（`app/services/market_source/snapshot._top_industries_from_quotes`），否则
"补上数据"会变成偷换口径。
"""
from __future__ import annotations

from scripts.research_off_main_theme_penalty import (
    _attach_main_industries,
    _top_industries,
)


class _Snap:
    def __init__(self, ms):
        self.market_snapshot = ms


def test_top_industries_matches_production_formula():
    # amount 单位=元；结构：score = amount_yi * (1 + clamp(pct/20, -0.2, 0.5))
    rows = [
        ("半导体", 3_000_000_000, 10.0),   # 30亿 * 1.5 = 45
        ("电气设备", 2_000_000_000, -10.0),  # 20亿 * 0.8 = 16
        ("元器件", 3_000_000_000, 0.0),    # 30亿 * 1.0 = 30
        ("钢铁", 10_000_000_000, 0.0),      # 100亿 * 1.0 = 100
        ("未分类", 999_000_000_000, 20.0),  # 未分类必须被跳过
    ]
    assert _top_industries(rows) == ["钢铁", "半导体", "元器件"]


def test_top_industries_clamps_extremes():
    # pct/20 上下夹在 [-0.2, 0.5]：+100% 不会放大到 6 倍，-100% 不会变成 0
    up = _top_industries([("A", 1_000_000_000, 100.0)])
    dn = _top_industries([("B", 1_000_000_000, -100.0)])
    assert up == ["A"] and dn == ["B"]
    # 0.5 上限：1亿*1.5=1.5 亿 vs 1亿*1.4=1.4 亿（若未夹紧，+100% 会是 1亿*6）
    assert _top_industries([("A", 1_000_000_000, 100.0), ("B", 1_400_000_000, -300.0)]) == ["A", "B"]


def test_attach_only_fills_missing_main_industries():
    day_with = _Snap({"sample_count": 100, "up_count": 60, "main_industries": ("已有",)})
    day_without = _Snap({"sample_count": 100, "up_count": 40})
    day_no_snap = _Snap({"sample_count": 100, "up_count": 50})
    groups = [
        ("2026-08-21", [], None, day_with),
        ("2026-09-11", [], None, day_without),
        ("2026-09-12", [], None, day_no_snap),
    ]
    db_map = {
        "2026-08-21": ["不应覆盖"],
        "2026-09-11": ["元器件", "通信设备", "半导体"],
        # 2026-09-12 缺数据 → 不得虚构
    }
    assert _attach_main_industries(groups, db_map) == 1
    assert day_with.market_snapshot["main_industries"] == ("已有",)
    assert day_without.market_snapshot["main_industries"] == ("元器件", "通信设备", "半导体")
    assert "main_industries" not in day_no_snap.market_snapshot
    # 数值中性：regime 判定字段未被改动
    for d in (day_with, day_without, day_no_snap):
        assert d.market_snapshot["sample_count"] == 100
