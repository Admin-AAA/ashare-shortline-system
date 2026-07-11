"""D 项北极星回归护栏 — 纯函数单测 + 闸门阈值/逻辑锁定（第 178 轮 self-improve, T3/D 防御).

背景: D 项是当前唯一核心北极星（胜率/盈亏比/最大亏损）。系统已强于目标，但多个
self-improve 实例并行加脆弱过滤器，存在过拟合风险。verify_d_guardrail.py 把 D 项目标
固化为 CI 硬闸门。本测试锁定：
1. _compute_d_cost 的净成本计算数学（扣 0.07%/笔后净胜率/净盈亏比/最大亏损）。
2. verify_d_guardrail 的阈值常量与 _check 闸门逻辑——任何调低阈值的改动都会被本测试拦下。
不依赖 DB 真实数据，纯函数确定性验证。
"""
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import self_check  # type: ignore[import-not-found]
import verify_d_guardrail  # type: ignore[import-not-found]

# (code, trade_date, label, executed_return)
ROWS = [
    ("600000", "2026-07-01", "success", 5.0),
    ("600001", "2026-07-02", "success", 3.0),
    ("600002", "2026-07-03", "failed", -2.0),
    ("600003", "2026-07-04", "success", 4.0),
    ("600004", "2026-07-05", "failed", -1.0),
    ("600005", "2026-07-06", "success", 2.0),
]


def test_compute_d_cost_full_math():
    """全量窗口净成本计算：扣 0.07% 后净胜率/净盈亏比/最大亏损/平均收益精确。"""
    m = self_check._compute_d_cost(ROWS)
    assert m["n"] == 6
    # 毛胜率 = 4/6 = 66.7%；净 = 扣 0.07 后 4 笔仍 >0 → 同 66.7%
    assert m["gross_wr"] == 66.7
    assert m["net_wr"] == 66.7
    # 毛盈亏比 = 14.0 / 3.0 = 4.67；净 = 13.72 / 3.14 = 4.37
    assert m["gross_pl"] == 4.67
    assert m["net_pl"] == 4.37
    # 最大亏损 = min(rets) = -2.0
    assert m["max_loss"] == -2.0
    # 净平均 = (4.93+2.93-2.07+3.93-1.07+1.93)/6 = 1.76
    assert m["net_avg"] == 1.76
    assert m["gross_avg"] == 1.83


def test_compute_d_cost_deduction_edge_zero():
    """成本扣除边界：净收益恰好 0（毛 +0.07）不算胜（x>0 严格），毛 +0.08 算胜。"""
    zero = self_check._compute_d_cost([("X", "2026-07-01", "failed", 0.07)])
    assert zero["net_wr"] == 0.0  # net=0.0 不计入胜
    win = self_check._compute_d_cost([("X", "2026-07-01", "success", 0.08)])
    assert win["net_wr"] == 100.0  # net=0.01 >0


def test_compute_d_cost_since_date_filter():
    """since_date 窗口：仅取 >= 日期的样本（ISO 串字典序）。"""
    m = self_check._compute_d_cost(ROWS, since_date="2026-07-04")
    assert m["n"] == 3
    assert m["gross_wr"] == 66.7  # [4.0, -1.0, 2.0] → 2 胜
    assert m["max_loss"] == -1.0


def test_compute_d_cost_trade_dates_filter():
    """trade_dates 窗口：仅取指定日期样本。"""
    m = self_check._compute_d_cost(ROWS, trade_dates={"2026-07-01", "2026-07-03"})
    assert m["n"] == 2
    assert m["gross_wr"] == 50.0  # [5.0, -2.0]
    assert m["gross_pl"] == 2.5
    assert m["net_pl"] == 2.38  # 4.93 / 2.07


def test_compute_d_cost_empty_returns_none():
    assert self_check._compute_d_cost([]) is None


def test_guardrail_thresholds_locked():
    """闸门阈值常量锁定为北极星目标——任何调低都会被本测试拦下。"""
    assert verify_d_guardrail.NET_WINRATE_FLOOR == 65.0
    assert verify_d_guardrail.NET_PL_RATIO_FLOOR == 2.0
    assert verify_d_guardrail.MAX_LOSS_FLOOR == -6.5
    assert verify_d_guardrail.FORWARD_NET_WINRATE_FLOOR == 60.0


def test_guardrail_check_logic():
    """_check 闸门判定逻辑：达标返回 True，破线返回 False。"""
    assert verify_d_guardrail._check("t", True, 70.0, 65.0) is True
    assert verify_d_guardrail._check("t", False, 50.0, 65.0) is False
    # 最大亏损方向：越接近 0 越好，-5.0 >= -6.5 视为达标
    assert verify_d_guardrail._check("ml", True, -5.0, -6.5) is True
    assert verify_d_guardrail._check("ml", False, -7.0, -6.5) is False
