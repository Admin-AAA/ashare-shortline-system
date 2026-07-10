"""Regression guard: self_check D 项净成本指标（盈亏比 + 净胜率 + 净平均收益，第167轮 T2）。

背景: D 项北极星目标定义为"扣费后须达标"(T2 step4)，但 self_check 只报毛胜率+最大亏损，
盈亏比(第三子指标)与净胜率缺失 → 1/3 核心攻击目标不可见，且毛值虚高 0.07%/笔交易成本。
_compute_d_cost 用纯函数（不碰 DB）在同一去重样本上扣 0.07%/笔算净指标，本测试锁定统计逻辑。

与 test_self_check_forward_d.py 正交：后者锁毛胜率/最大亏损窗口逻辑（保持基线对比连续性），
本测试锁净成本派生指标。
"""
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]

# rows: (code, trade_date, label, executed_return)  — executed_return 已含滑点未扣佣金+印花税
_ROWS = [
    ("000001", "2026-05-22", "success", 3.0),
    ("000002", "2026-05-22", "failed", -5.7),
    ("000003", "2026-06-01", "success", 4.0),
    ("000004", "2026-07-07", "failed", -5.7),   # 修复前污染
    ("000005", "2026-07-07", "failed", -5.7),   # 修复前污染
    ("000006", "2026-07-09", "success", 5.0),   # 前瞻
    ("000007", "2026-07-10", "success", 6.0),   # 前瞻
]


def test_all_time_cost_metrics():
    m = self_check._compute_d_cost(_ROWS)
    assert m is not None
    assert m["n"] == 7
    # 毛/净胜率：4 笔正收益 / 7，无介于 (0, 0.07] 的边际胜率 → 相等
    assert m["gross_wr"] == 57.1
    assert m["net_wr"] == 57.1
    # 盈亏比 = 盈利和 / 亏损和
    assert m["gross_pl"] == 1.05   # 18.0 / 17.1
    assert m["net_pl"] == 1.02     # 17.72 / 17.31
    # 平均收益：毛 0.90/7=0.13；净再扣 0.07/笔 = 0.06
    assert m["gross_avg"] == 0.13
    assert m["net_avg"] == 0.06
    assert m["max_loss"] == -5.7


def test_cost_drag_lowers_net():
    """净口径应 = 毛口径扣 0.07%/笔（平均收益），且净胜率不高于毛胜率。"""
    m = self_check._compute_d_cost(_ROWS)
    assert m["net_avg"] == round(m["gross_avg"] - self_check.ADDITIONAL_COST_PCT, 2)
    assert m["net_wr"] <= m["gross_wr"]


def test_marginal_win_flips_to_loss_net_of_cost():
    """收益 0.05% 毛算胜、净(扣0.07%)算亏 → 净胜率应低于毛胜率，暴露成本侵蚀。"""
    rows = [
        ("000001", "2026-07-10", "success", 3.0),
        ("000002", "2026-07-10", "success", 0.05),  # 边际胜率：毛赢净亏
    ]
    m = self_check._compute_d_cost(rows)
    assert m["gross_wr"] == 100.0
    assert m["net_wr"] == 50.0
    assert m["net_pl"] < m["gross_pl"]


def test_forward_epoch_matches_forward_window():
    """前瞻窗口(>=2026-07-09)剔修复前污染：仅 000006/000007 两笔皆正 → 净胜率100%。"""
    fwd = self_check._compute_d_cost(_ROWS, since_date="2026-07-09")
    assert fwd is not None
    assert fwd["n"] == 2
    assert fwd["net_wr"] == 100.0
    assert fwd["gross_pl"] == 999.0   # 无亏损 → 盈亏比封顶
    assert fwd["net_pl"] == 999.0


def test_empty_window_returns_none():
    assert self_check._compute_d_cost(_ROWS, since_date="2030-01-01") is None


# rows: (code, trade_date, label, executed_return) — 混合 20cm(创业板/科创板) 与 10cm(主板)
_BOARD_ROWS = [
    ("300001", "2026-07-10", "success", 5.0),    # 20cm 创业板 胜
    ("300002", "2026-07-10", "success", 4.0),    # 20cm 创业板 胜
    ("688001", "2026-07-10", "failed", -5.69),   # 20cm 科创板 亏（止损地板）
    ("000001", "2026-07-10", "success", 3.0),    # 10cm 主板 胜
    ("600001", "2026-07-10", "failed", -5.88),   # 10cm 主板 亏（止损地板）
    ("600002", "2026-07-10", "failed", -5.0),    # 10cm 主板 亏
]


def test_board_breakdown_splits_correctly():
    """板块拆分应按代码前缀(300/301/688/689=20cm)正确归类并独立统计。"""
    bd = self_check._compute_d_board_breakdown(_BOARD_ROWS)
    c20 = bd["20cm"]
    c10 = bd["10cm"]
    assert c20 is not None and c10 is not None
    assert c20["n"] == 3 and c10["n"] == 3
    assert c20["win_rate"] == 66.7          # 2/3
    assert c10["win_rate"] == 33.3          # 1/3
    # maxloss 两类板块均为止损地板（~ -5.7%），证明板块非 maxloss 归因因子
    assert c20["max_loss"] == -5.69
    assert c10["max_loss"] == -5.88
    assert c20["avg"] == 1.10               # (5+4-5.69)/3
    assert c10["avg"] == -2.63             # (3-5.88-5)/3


def test_board_breakdown_missing_board_is_none():
    """某板块无样本时返回 None（不报错），护栏可安全判空。"""
    only_10cm = [r for r in _BOARD_ROWS if not r[0].startswith(("300", "301", "688", "689"))]
    bd = self_check._compute_d_board_breakdown(only_10cm)
    assert bd["10cm"] is not None
    assert bd["20cm"] is None


def test_board_guardrail_strongest_segment():
    """护栏核心断言：20cm 是胜率最高分段（非风险源）→ 后续轮次不得对其降权/加闸门。"""
    bd = self_check._compute_d_board_breakdown(_BOARD_ROWS)
    assert bd["20cm"]["win_rate"] > bd["10cm"]["win_rate"]
