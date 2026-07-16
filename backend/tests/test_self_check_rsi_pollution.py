"""Regression guard: self_check D 项 RSI 数据污染影响诊断（第162轮 T3 度量透明化）。

背景: D 项全量胜率含历史 RSI 污染样本（rsi_6=0 & rsi_12=50，上游 stk_factor 缺失
被 _f(None,0.0) 写入）。这些样本选股时用了错的 RSI，不反映策略真实能力，拉低胜率
口径（实证：干净 WR 77.1% vs 污染 WR 60.5%，差 16.6pp）。但不剔除主口径（避免
系统性刷数字）——只报告影响差，让运营者区分「策略退化」vs「数据污染扭曲」，
防止误判循环加规则（第164轮过拟合护栏）。

_rsi_pollution_split 是纯函数：按 rsi_6=0 & rsi_12=50 拆分去重样本，返回
(clean_wr, polluted_wr, clean_n, polluted_n)。无 clean 或无 polluted 返回 None。
"""
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]


def test_split_returns_none_when_empty():
    """无样本 → None。"""
    assert self_check._rsi_pollution_split([]) is None


def test_split_returns_none_when_no_clean():
    """全是污染样本（无干净）→ None（无法对比）。"""
    rows = [('600001', '2026-07-02', 'failed', 0.0, 50.0),
            ('600002', '2026-07-02', 'success', 0.0, 50.0)]
    assert self_check._rsi_pollution_split(rows) is None


def test_split_returns_none_when_no_polluted():
    """全是干净样本（无污染）→ None（无需报告影响）。"""
    rows = [('600001', '2026-07-09', 'success', 55.0, 60.0),
            ('600002', '2026-07-09', 'failed', 45.0, 50.0)]
    assert self_check._rsi_pollution_split(rows) is None


def test_split_computes_clean_and_polluted_wr():
    """3 干净（2 成功）+ 2 污染（1 成功）→ clean_wr=66.7, polluted_wr=50.0。"""
    rows = [
        ('600001', '2026-07-09', 'success', 55.0, 60.0),   # clean win
        ('600002', '2026-07-09', 'success', 50.0, 55.0),   # clean win
        ('600003', '2026-07-09', 'failed',  45.0, 50.0),   # clean fail (rsi12=50 but rsi6=45≠0)
        ('600004', '2026-07-02', 'success', 0.0,  50.0),  # polluted win
        ('600005', '2026-07-02', 'failed',  0.0,  50.0),  # polluted fail
    ]
    result = self_check._rsi_pollution_split(rows)
    assert result is not None
    clean_wr, polluted_wr, clean_n, polluted_n = result
    assert clean_n == 3
    assert polluted_n == 2
    assert clean_wr == 66.7
    assert polluted_wr == 50.0


def test_split_treats_none_rsi_as_zero_for_comparison():
    """rsi6/rsi12 为 None（解析失败）按 0 处理——与 DB MAX(JSON_EXTRACT) 缺失返回 NULL 对齐。"""
    rows = [
        ('600001', '2026-07-09', 'success', 55.0, 60.0),   # clean
        ('600002', '2026-07-02', 'failed',  None, None),   # None→0, 但 rsi12=None→0≠50 → clean
    ]
    # rsi12=None→0≠50，所以 600002 不算污染，归入 clean → 无污染样本 → None
    assert self_check._rsi_pollution_split(rows) is None
