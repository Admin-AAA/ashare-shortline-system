"""Regression guard: self_check D 项单日 PF 拖累护栏（_single_day_pl_drag，第191轮 T3）。

背景：D 项近10日净盈亏比 1.65 低于基线 1.94，但逐日拆解发现 07-16 单日
n=55 / WR 45.5% / PF 0.53（全表唯一 PF<1 大样本日），剔除后近10日 PF 回升到 1.77。
07-16 是冰点被误判活跃的情绪异常日（canonical L-004 / no_follow_through 子簇护栏同指），
个股 entry 侧不可解、禁止加规则。若不显式诊断，运营者看到 PF<基线 会误判真退化、
循环加 entry 规则过拟合单日噪声。_single_day_pl_drag 用纯函数锁定该判定逻辑。
"""
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]

COST = self_check.ADDITIONAL_COST_PCT  # 0.07


def _mk(date, ret):
    return ("000001", date, "success" if ret > 0 else "failed", ret)


def test_single_day_drag_detected():
    """单日大额亏损主导窗口 → 返回拖累诊断，禁止加 entry 规则。"""
    # 9 个正常日各 +2%（净 1.93），1 个异常日 5 笔 -5.7%（净 -5.77）
    rows = []
    for i in range(9):
        rows.append(_mk(f"2026-07-{10+i:02d}", 2.0))
    for j in range(5):
        rows.append(_mk("2026-07-20", -5.7))
    dates = {r[1] for r in rows}
    drag = self_check._single_day_pl_drag(rows, dates)
    assert drag is not None
    assert drag["worst_day"] == "2026-07-20"
    # 该日亏损 5×5.77=28.85 占全部亏损 28.85（唯一亏损日）→ 100%
    assert drag["day_loss_share"] == 100.0
    assert drag["pf_without"] > drag["pf_with"]
    assert drag["recover"] >= 0.15


def test_uniform_losses_no_drag():
    """亏损均匀分布在多日（无单一主导日）→ None，PF 下滑需正常排查（可能真退化）。"""
    # 每日 1 胜(+2) 1 亏(-3)，亏损分散，单日占比 ~10% < 40%
    rows = []
    for i in range(10):
        rows.append(_mk(f"2026-07-{10+i:02d}", 2.0))
        rows.append(_mk(f"2026-07-{10+i:02d}", -3.0))
    dates = {r[1] for r in rows}
    assert self_check._single_day_pl_drag(rows, dates) is None


def test_insufficient_recover_no_drag():
    """剔除最大亏损日后 PF 回升 <0.15 → None（拖累不显著，不豁免排查）。"""
    # 窗口 PF 本来接近 1（盈利≈亏损），多日均亏；worst_day 占 40% 但剔除后 PF 仅从
    # 1.00→1.08（recover≈0.08<0.15），亏损是普遍现象而非单日主导 → None，需正常排查。
    # 盈利合计 150（+3×50）；07-20 亏 40（占40%），07-21/07-22 各亏 30、07-23 亏 20。
    rows = [_mk("2026-06-28", 3.0) for _ in range(50)]           # 盈利 50×2.93=146.5
    rows += [_mk("2026-07-20", -0.60) for _ in range(50)]        # 亏 50×0.67=33.5（worst，~40%）
    rows += [_mk("2026-07-21", -0.50) for _ in range(50)]        # 亏 50×0.57=28.5
    rows += [_mk("2026-07-22", -0.40) for _ in range(50)]        # 亏 50×0.47=23.5
    dates = {r[1] for r in rows}
    drag = self_check._single_day_pl_drag(rows, dates)
    assert drag is None


def test_all_wins_no_drag():
    """窗口内无亏损 → None（total_loss<=0 分支）。"""
    rows = [_mk("2026-07-10", 2.0), _mk("2026-07-11", 3.0)]
    assert self_check._single_day_pl_drag(rows, {r[1] for r in rows}) is None


def test_empty_window_returns_none():
    assert self_check._single_day_pl_drag([], set()) is None
    rows = [_mk("2026-07-10", 2.0)]
    assert self_check._single_day_pl_drag(rows, {"2026-07-11"}) is None  # 日期不在窗口


def test_none_return_skipped():
    """executed_return=None 的样本被跳过，不污染诊断。"""
    rows = [_mk("2026-07-10", 2.0), ("000002", "2026-07-10", "failed", None)]
    # 唯一有值的样本是盈利 → 无亏损 → None
    assert self_check._single_day_pl_drag(rows, {"2026-07-10"}) is None
