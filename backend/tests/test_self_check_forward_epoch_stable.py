"""Regression guard: self_check 前瞻纪元稳定化 + 窗口过窄护栏（第195轮 T2）。

背景: 原 _resolve_forward_epoch 用纯动态 git epoch（取最近一次改 strategy.yaml/
aggregator.py 的 commit 之后首个 formal 交易日）。self-improve 每轮几乎都提交规则变更，
且近期多个交易日为保护性 0 推荐（无交易结算），导致动态 epoch 永远落在"今天之后"→
前瞻窗口恒为空→D 项永远无法做样本外(OOS)验证（违反北极星 D 护栏）；且 epoch=None 时
E 项前瞻列因 `trade_date >= NULL` 恒 0 而误报"前瞻0笔(等结算)"。

修复: 动态 epoch 仅当其之后已累积 ≥MIN_OOS_SAMPLE 笔结算样本才采用纯窗口，否则回退
稳定基线 FORWARD_EPOCH_PINNED；并新增 _forward_window_day_spread 检测窗口过窄
（单日/受污染 regime 主导），防止把单日噪声误读为 D 项退化。

本测试锁定两个纯函数逻辑，不依赖 DB / git 真实数据。
"""
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]

# rows: (code, trade_date, label, executed_return)
_ROWS = [
    ("000001", "2026-07-14", "success", 3.0),
    ("000002", "2026-07-14", "failed", -5.7),
    ("000003", "2026-07-14", "success", 4.0),  # 全在 07-14 单一交易日
    ("000004", "2026-07-15", "success", 5.0),
    ("000005", "2026-07-15", "failed", -5.7),
    ("000006", "2026-07-16", "success", 6.0),
    ("000007", "2026-07-17", "success", 2.0),  # epoch 之后
]


def test_pick_epoch_falls_back_when_dynamic_empty():
    """动态 epoch 为 None → 回退稳定基线。"""
    assert self_check._pick_forward_epoch(None, 0) == self_check.FORWARD_EPOCH_PINNED


def test_pick_epoch_falls_back_when_insufficient_samples():
    """动态 epoch 之后样本 < MIN_OOS_SAMPLE → 回退稳定基线（窗口未累积）。"""
    assert self_check._pick_forward_epoch("2026-07-21", self_check.MIN_OOS_SAMPLE - 1) \
        == self_check.FORWARD_EPOCH_PINNED


def test_pick_epoch_uses_dynamic_when_sufficient():
    """动态 epoch 之后样本 ≥ MIN_OOS_SAMPLE → 采用纯窗口。"""
    assert self_check._pick_forward_epoch("2026-07-21", self_check.MIN_OOS_SAMPLE) == "2026-07-21"
    assert self_check._pick_forward_epoch("2026-07-21", 50) == "2026-07-21"


def test_day_spread_single_day_is_narrow():
    """全部样本集中于单一交易日 → distinct=1，占比 100%，触发过窄护栏。"""
    rows = [("a", "2026-07-16", "success", 1.0),
            ("b", "2026-07-16", "failed", -5.0),
            ("c", "2026-07-16", "success", 2.0)]
    distinct, share, dom = self_check._forward_window_day_spread(rows, "2026-07-14")
    assert distinct == 1
    assert share == 1.0
    assert dom == "2026-07-16"


def test_day_spread_multi_day_not_narrow():
    """样本分布于 3 个交易日 → distinct=3，单日占比 <1。"""
    distinct, share, dom = self_check._forward_window_day_spread(_ROWS, "2026-07-14")
    assert distinct == 4  # 07-14/15/16/17
    assert share < 1.0
    assert dom in ("2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17")


def test_day_spread_only_counts_epoch_onward():
    """早于 epoch 的样本不计入窗口分布。"""
    rows = [("a", "2026-07-01", "success", 1.0),   # 早于 epoch
            ("b", "2026-07-16", "success", 2.0),
            ("c", "2026-07-16", "failed", -5.0)]
    distinct, share, dom = self_check._forward_window_day_spread(rows, "2026-07-14")
    assert distinct == 1  # 仅 07-16
    assert dom == "2026-07-16"


def test_day_spread_empty():
    """epoch 之后无样本 → distinct=0。"""
    rows = [("a", "2026-07-01", "success", 1.0)]
    distinct, share, dom = self_check._forward_window_day_spread(rows, "2026-07-14")
    assert distinct == 0
    assert dom is None
