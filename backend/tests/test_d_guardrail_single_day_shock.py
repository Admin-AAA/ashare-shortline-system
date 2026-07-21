"""D 项护栏 — 单日 regime 冲击护栏纯函数单测（第 N 轮 self-improve, T2 防御）.

背景: 文档化护栏（第187/192轮）规定单日失败高度集中属市场 regime 冲击、禁止据此加 entry
规则。此前该护栏仅在 self_check 文本告警，本测试锁定 verify_d_guardrail 中把它固化为
机器可读信号的纯函数逻辑（_single_day_failure_share / _eval_regime_shock），使自动化
编排可读取 regime_shock.shock 并拒绝对单日冲击过拟合加 entry 规则。

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

import verify_d_guardrail  # type: ignore[import-not-found]

# rows: (code, trade_date, label, executed_return)；失败 = label != 'success'


def test_single_day_failure_share_no_failures():
    """窗口内无失败：share=0.0，top_date=None。"""
    rows = [
        ("600000", "2026-07-10", "success", 5.0),
        ("600001", "2026-07-11", "success", 3.0),
    ]
    dates = {"2026-07-10", "2026-07-11"}
    s = verify_d_guardrail._single_day_failure_share(rows, dates)
    assert s["total_failures"] == 0
    assert s["share"] == 0.0
    assert s["top_date"] is None
    assert s["n"] == 2


def test_single_day_failure_share_spread():
    """失败分散在多日：top_date 取失败最多日，share 正确。"""
    rows = [
        ("600000", "2026-07-10", "failed", -2.0),
        ("600001", "2026-07-11", "failed", -1.0),
        ("600002", "2026-07-11", "failed", -1.0),
        ("600003", "2026-07-12", "success", 4.0),
    ]
    dates = {"2026-07-10", "2026-07-11", "2026-07-12"}
    s = verify_d_guardrail._single_day_failure_share(rows, dates)
    assert s["total_failures"] == 3
    assert s["top_date"] == "2026-07-11"
    assert s["top_failures"] == 2
    assert s["share"] == round(2 / 3, 2)


def test_eval_regime_shock_below_threshold():
    """失败分散（单日占比 < 40%）：不触发单日冲击。"""
    # 10 天各 1 失败 = 单日最多 1/10 = 10% < 40%
    rows = [(f"60000{i}", f"2026-07-{10 + i:02d}", "failed", -1.0) for i in range(10)]
    dates = {f"2026-07-{10 + i:02d}" for i in range(10)}
    r = verify_d_guardrail._eval_regime_shock(rows, dates)
    assert r["shock"] is False
    assert r["share"] == 0.1
    assert r["msg"] == ""


def test_eval_regime_shock_above_threshold():
    """单日集中 5/8 失败（62.5% ≥ 40%，总数 8 ≥ 5）：触发单日冲击且 msg 含禁止语。"""
    rows = [
        ("600000", "2026-07-16", "failed", -5.0),
        ("600001", "2026-07-16", "failed", -4.0),
        ("600002", "2026-07-16", "failed", -3.0),
        ("600003", "2026-07-16", "failed", -2.0),
        ("600004", "2026-07-16", "failed", -1.0),
        ("600005", "2026-07-16", "success", 2.0),
        ("600006", "2026-07-17", "failed", -2.0),
        ("600007", "2026-07-18", "failed", -1.0),
        ("600008", "2026-07-19", "failed", -1.5),
        ("600009", "2026-07-20", "success", 3.0),
    ]
    dates = {f"2026-07-{d}" for d in (16, 17, 18, 19, 20)}
    r = verify_d_guardrail._eval_regime_shock(rows, dates)
    assert r["shock"] is True
    assert r["top_date"] == "2026-07-16"
    assert r["top_failures"] == 5
    assert r["total_failures"] == 8
    assert r["share"] == 0.62  # round(5/8, 2) = 0.62（round-half-to-even）
    assert "禁止" in r["msg"]
    assert "2026-07-16" in r["msg"]


def test_eval_regime_shock_insufficient_total():
    """单日 100% 但失败总数 < 阈值（3 < 5）：不触发（避免小样本误报）。"""
    rows = [
        ("600000", "2026-07-16", "failed", -2.0),
        ("600001", "2026-07-16", "failed", -1.0),
        ("600002", "2026-07-16", "failed", -1.5),
        ("600003", "2026-07-17", "success", 3.0),
    ]
    dates = {"2026-07-16", "2026-07-17"}
    r = verify_d_guardrail._eval_regime_shock(rows, dates)
    assert r["shock"] is False
    assert r["total_failures"] == 3
    assert "不足以断言" in r["msg"]


def test_eval_regime_shock_integration_with_recent_dates():
    """端到端：_recent_trade_dates 取近 5 日 + _eval_regime_shock 正确判定冲击。"""
    rows = [
        ("600000", "2026-07-16", "failed", -5.0),
        ("600001", "2026-07-16", "failed", -4.0),
        ("600002", "2026-07-16", "failed", -3.0),
        ("600003", "2026-07-17", "failed", -2.0),
        ("600004", "2026-07-18", "failed", -1.0),
        ("600005", "2026-07-19", "success", 2.0),
        ("600006", "2026-07-05", "success", 1.0),  # 窗口外（旧日）
        ("600007", "2026-07-06", "failed", -1.0),  # 窗口外
    ]
    recent = verify_d_guardrail._recent_trade_dates(rows, k=5)
    # 6 个独立交易日取最近 5 个：仅最旧的 07-05 落窗口外，07-06 仍在窗口内
    assert "2026-07-05" not in recent
    assert "2026-07-06" in recent
    assert "2026-07-16" in recent
    r = verify_d_guardrail._eval_regime_shock(rows, recent)
    assert r["shock"] is True
    # 窗口失败 = 07-16(3) + 07-17 + 07-18 + 07-06 = 6；07-16 占 3/6 = 0.5
    assert r["total_failures"] == 6
    assert r["share"] == 0.5
