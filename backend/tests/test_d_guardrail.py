"""D 项北极星回归护栏 — 纯函数单测 + 闸门阈值/逻辑锁定（第 178 轮 self-improve, T3/D 防御).

背景: D 项是当前唯一核心北极星（胜率/盈亏比/最大亏损）。系统已强于目标，但多个
self-improve 实例并行加脆弱过滤器，存在过拟合风险。verify_d_guardrail.py 把 D 项目标
固化为 CI 硬闸门。本测试锁定：
1. _compute_d_cost 的净成本计算数学（扣 0.07%/笔后净胜率/净盈亏比/最大亏损）。
2. verify_d_guardrail 的阈值常量与 _check 闸门逻辑——任何调低阈值的改动都会被本测试拦下。
不依赖 DB 真实数据，纯函数确定性验证。
"""
import json
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


def test_build_report_structure_and_thresholds():
    """build_report 结构锁定：含 full/forward/passed/thresholds，前瞻块含 n。"""
    full = {"n": 187, "net_wr": 75.4, "net_pl": 2.98, "max_loss": -5.88}
    fwd = {"n": 31, "net_wr": 77.4, "net_pl": 4.19, "max_loss": -4.2}
    rep = verify_d_guardrail.build_report(full, fwd, True, 0)
    assert rep["passed"] is True
    assert rep["exit_code"] == 0
    assert rep["full"] == full
    assert rep["forward"] == fwd
    assert rep["thresholds"]["net_winrate_floor"] == 65.0
    assert rep["thresholds"]["forward_net_winrate_floor"] == 60.0
    assert isinstance(rep["generated_at"], str) and "T" in rep["generated_at"]


def test_build_report_handles_empty_samples():
    """样本不足（None）时报告仍可结构化，不抛异常，passed=False/exit=2。"""
    rep = verify_d_guardrail.build_report(None, None, False, 2)
    assert rep["full"] is None
    assert rep["forward"] is None
    assert rep["passed"] is False
    assert rep["exit_code"] == 2


def test_write_report_roundtrip(tmp_path):
    """write_report 落盘 JSON 可被回读且内容一致（跨 run 漂移追踪的基础）。"""
    path = tmp_path / "d_guardrail_report.json"
    full = {"n": 10, "net_wr": 70.0, "net_pl": 3.0, "max_loss": -5.0}
    fwd = {"n": 5, "net_wr": 65.0, "net_pl": 2.5, "max_loss": -4.0}
    rep = verify_d_guardrail.build_report(full, fwd, True, 0)
    verify_d_guardrail.write_report(rep, str(path))
    assert path.exists()
    back = json.loads(path.read_text(encoding="utf-8"))
    assert back == rep


def test_recent_trade_dates_returns_k_most_recent():
    """_recent_trade_dates 去重后倒序取最近 k 个独立交易日。"""
    rows = [
        ("A", "2026-07-01", "success", 1.0),
        ("B", "2026-07-06", "success", 1.0),
        ("C", "2026-07-06", "failed", -1.0),  # 同日重复应去重
        ("D", "2026-07-04", "success", 1.0),
        ("E", "2026-07-05", "success", 1.0),
        ("F", "2026-07-03", "success", 1.0),
        ("G", "2026-07-02", "success", 1.0),
    ]
    all_dates = verify_d_guardrail._recent_trade_dates(rows, k=10)
    assert all_dates == {"2026-07-01", "2026-07-02", "2026-07-03",
                         "2026-07-04", "2026-07-05", "2026-07-06"}
    top3 = verify_d_guardrail._recent_trade_dates(rows, k=3)
    assert top3 == {"2026-07-06", "2026-07-05", "2026-07-04"}


def test_eval_recent_drift_triggers_on_significant_drop():
    """近窗口净盈亏比 < 全量*0.80 且 n>=RECENT_MIN_N → 早警触发。"""
    full = {"n": 200, "net_wr": 68.9, "net_pl": 2.08, "max_loss": -5.9}
    recent = {"n": 100, "net_wr": 65.0, "net_pl": 1.65, "max_loss": -5.9}
    d = verify_d_guardrail._eval_recent_drift(full, recent)
    assert d["warn"] is True
    assert d["ratio"] == round(1.65 / 2.08, 2)
    assert "no_follow_through" in d["msg"] and "gap_roundtrip" in d["msg"]


def test_eval_recent_drift_no_trigger_healthy_ratio():
    """近窗口净盈亏比接近全量（比值>=0.80）→ 不触发。"""
    full = {"n": 200, "net_wr": 68.9, "net_pl": 2.08, "max_loss": -5.9}
    recent = {"n": 100, "net_wr": 67.0, "net_pl": 2.0, "max_loss": -5.9}
    d = verify_d_guardrail._eval_recent_drift(full, recent)
    assert d["warn"] is False
    assert d["ratio"] == round(2.0 / 2.08, 2)


def test_eval_recent_drift_no_trigger_small_sample():
    """近窗口样本 < RECENT_MIN_N → 不触发（避免小样本误报），msg 说明原因。"""
    full = {"n": 200, "net_wr": 68.9, "net_pl": 2.08, "max_loss": -5.9}
    recent = {"n": 20, "net_wr": 50.0, "net_pl": 1.0, "max_loss": -5.9}
    d = verify_d_guardrail._eval_recent_drift(full, recent)
    assert d["warn"] is False
    assert "不足" in d["msg"]


def test_build_report_includes_recent_window_and_drift():
    """build_report 新增 recent_10d + recent_drift 字段且结构正确。"""
    full = {"n": 200, "net_wr": 68.9, "net_pl": 2.08, "max_loss": -5.9}
    fwd = {"n": 31, "net_wr": 77.4, "net_pl": 4.19, "max_loss": -4.2}
    recent = {"n": 100, "net_wr": 65.0, "net_pl": 1.65, "max_loss": -5.9}
    drift = {"warn": True, "ratio": 0.79, "msg": "x"}
    rep = verify_d_guardrail.build_report(full, fwd, True, 0, recent=recent, drift=drift)
    assert rep["recent_10d"] == recent
    assert rep["recent_drift"] == drift
    assert rep["thresholds"]["recent_window_days"] == verify_d_guardrail.RECENT_WINDOW_DAYS
    assert rep["thresholds"]["recent_pl_drag_ratio"] == verify_d_guardrail.RECENT_PL_DRAG_RATIO
    assert rep["thresholds"]["recent_min_n"] == verify_d_guardrail.RECENT_MIN_N


def test_build_report_recent_defaults_when_omitted():
    """build_report 不传 recent/drift 时回填安全默认（向后兼容既有 4 参数调用）。"""
    full = {"n": 10, "net_wr": 70.0, "net_pl": 3.0, "max_loss": -5.0}
    fwd = {"n": 5, "net_wr": 65.0, "net_pl": 2.5, "max_loss": -4.0}
    rep = verify_d_guardrail.build_report(full, fwd, True, 0)
    assert rep["recent_10d"] is None
    assert rep["recent_drift"] == {"warn": False, "ratio": None, "msg": ""}
