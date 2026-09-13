"""Regression guard: self_check D 项多窗口（全量/近20交易日/前瞻）统计逻辑（第164轮 T3）。

背景: D 项全量胜率被"修复前历史污染"拉低（如 2026-07-07/07-08 在去重与弱市否决
commit 之前生成的 run）。每轮 self-improve 若只看全量 57.7% 会误判退化、循环加规则
过拟合。_compute_d_window 用纯函数（不碰 DB）在已去重样本上按窗口算
(win_rate, n, max_loss)，让前瞻窗口可见，避免误读。

本测试锁定纯统计逻辑，不依赖 DB 真实数据。
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
    ("000001", "2026-05-22", "success", 3.0),
    ("000002", "2026-05-22", "failed", -5.7),
    ("000003", "2026-06-01", "success", 4.0),
    ("000004", "2026-07-07", "failed", -5.7),  # 修复前污染
    ("000005", "2026-07-07", "failed", -5.7),  # 修复前污染
    ("000006", "2026-07-09", "success", 5.0),  # 前瞻
    ("000007", "2026-07-10", "success", 6.0),  # 前瞻
]


def test_all_time_window():
    res = self_check._compute_d_window(_ROWS)
    assert res is not None
    wr, n, ml = res
    assert n == 7
    assert wr == round(4 * 100 / 7, 1)  # 4 wins / 7
    assert ml == -5.7


def test_forward_epoch_excludes_prefix_contamination():
    """前瞻窗口(>=2026-07-09)剔除 07-07 修复前污染 → 胜率应高于全量。"""
    all_res = self_check._compute_d_window(_ROWS)
    fwd = self_check._compute_d_window(_ROWS, since_date="2026-07-09")
    assert fwd is not None
    # 前瞻: 000006,000007 两笔皆 success → WR=100%
    assert fwd[0] == 100.0
    assert fwd[1] == 2
    # 前瞻胜率显著高于全量（证明全量下滑是历史污染）
    assert fwd[0] > all_res[0]


def test_trade_dates_window():
    """trade_dates 过滤：仅近 20 交易日集合。"""
    dates = sorted({r[1] for r in _ROWS})
    last2 = set(dates[-2:])
    res = self_check._compute_d_window(_ROWS, trade_dates=last2)
    assert res is not None
    assert res[1] == 2  # 仅 07-09, 07-10 两笔
    assert res[0] == 100.0


def test_empty_window_returns_none():
    """无样本窗口返回 None（self_check 打印"样本不足"而非崩溃）。"""
    res = self_check._compute_d_window(_ROWS, since_date="2030-01-01")
    assert res is None


# ── L-099：D 项待结算管道（防「n 不变 = 系统停摆/闸门饿死」误读）─────────────

def test_pending_pipeline_stats_basic():
    rows = [("2026-09-09", 7), ("2026-09-10", 8), ("2026-09-11", 10)]
    st = self_check._pending_pipeline_stats(rows)
    assert st == {
        "total_n": 25, "n_days": 3,
        "first_date": "2026-09-09", "last_date": "2026-09-11",
        "by_day": {"2026-09-09": 7, "2026-09-10": 8, "2026-09-11": 10},
    }


def test_pending_pipeline_stats_empty_and_zero():
    assert self_check._pending_pipeline_stats([]) is None
    assert self_check._pending_pipeline_stats([("2026-09-09", 0)]) is None


def test_pending_pipeline_stats_sorts_and_coerces():
    st = self_check._pending_pipeline_stats(
        [("2026-09-11", "10"), ("2026-09-09", 7)])
    assert st["first_date"] == "2026-09-09"
    assert st["by_day"]["2026-09-11"] == 10
