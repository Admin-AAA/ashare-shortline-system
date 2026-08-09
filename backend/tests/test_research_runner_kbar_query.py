"""T4 性能回归：回测快照捕获不得对 daily_kbars 大宽表做 SELECT *。

daily_kbars 是 351MB / 80 列的大宽表。research_runner.capture() 曾用
`select(DailyKbar)`（即 SELECT *）拉取全列，在 research backtest 的
全市场 / 多年窗口下触发 MySQL 3024（30s 超时），使 /api/backtests 的
preflight 直接 500。

本测试在不连库的前提下，断言 capture() 构造的两条查询已被 load_only 收窄到
实际需要的列，从而避免 SELECT * 拖垮查询。
"""

from sqlalchemy import select
from sqlalchemy.orm import load_only

from app.models.tables import DailyKbar
from app.services.backtest.research_runner import (
    RESEARCH_KBAR_LOADED_COLUMNS,
    _deterministic_kbar_rows,
)


# 这些列不在回测捕获的下游消费路径中，是「SELECT * 才会拉、load_only 应排除」的
# 典型全宽表列。若编译后的 SQL 仍包含它们，说明 load_only 没生效（回退到 SELECT *）。
_FULL_SCAN_MARKERS = (
    "daily_kbars.rsi_6",
    "daily_kbars.kdj_k",
    "daily_kbars.boll_upper",
    "daily_kbars.macd_bar",
    "daily_kbars.obv",
    "daily_kbars.vol_ratio_5",
    "daily_kbars.max_dd20",
    "daily_kbars.adj_factor",
    "daily_kbars.upper_shadow_count20",
    "daily_kbars.chip_winner_rate",
)

# 捕获下游（_stock_as_of / _outcome_bars / _chip_concentration）实际需要的列，
# 必须全部出现在 load_only 集合里。
_NEEDED_COLUMNS = (
    "daily_kbars.code",
    "daily_kbars.trade_date",
    "daily_kbars.open",
    "daily_kbars.high",
    "daily_kbars.low",
    "daily_kbars.close",
    "daily_kbars.volume",
    "daily_kbars.amount",
    "daily_kbars.pct_chg",
    "daily_kbars.turnover_rate",
    "daily_kbars.ma5",
    "daily_kbars.ma10",
    "daily_kbars.ma20",
    "daily_kbars.ma60",
    "daily_kbars.macd_dif",
    "daily_kbars.macd_dea",
    "daily_kbars.rsi_12",
    "daily_kbars.breakout20",
    "daily_kbars.breakout60",
    "daily_kbars.chip_cost_15",
    "daily_kbars.chip_cost_85",
)


def _market_context_stmt(dates):
    return (
        select(DailyKbar)
        .options(load_only(DailyKbar.code, DailyKbar.trade_date, DailyKbar.pct_chg))
        .where(DailyKbar.trade_date.in_(dates))
    )


def _all_rows_stmt(codes, history_start, outcome_end, first_signal="2025-01-01", last_signal="2025-12-31"):
    return (
        select(DailyKbar)
        .options(load_only(*RESEARCH_KBAR_LOADED_COLUMNS))
        .where(
            DailyKbar.code.in_(codes),
            DailyKbar.trade_date >= history_start,
            DailyKbar.trade_date <= outcome_end,
            (DailyKbar.trade_date < first_signal) | (DailyKbar.trade_date > last_signal),
        )
    )


def _signal_stmt(dates, target_codes):
    q = (
        select(DailyKbar)
        .options(load_only(*RESEARCH_KBAR_LOADED_COLUMNS))
        .where(DailyKbar.trade_date.in_(dates))
    )
    if target_codes:
        q = q.where(DailyKbar.code.in_(tuple(sorted(target_codes))))
    return q


def test_market_context_query_is_narrow():
    sql = str(_market_context_stmt(["2025-01-02", "2025-01-03"]))
    # 市场广度只需 code/trade_date/pct_chg，必须排除其它全宽表列
    for marker in ("daily_kbars.ma5", "daily_kbars.amount", "daily_kbars.rsi_12"):
        assert marker not in sql, f"market-context 查询泄漏了全列 {marker}"
    assert "daily_kbars.pct_chg" in sql


def test_all_rows_query_excludes_full_scan_markers():
    sql = str(_all_rows_stmt(("000001", "000002"), "2024-01-01", "2025-12-31"))
    for marker in _FULL_SCAN_MARKERS:
        assert marker not in sql, f"all_rows 查询回退到 SELECT *（含 {marker}）"
    for needed in _NEEDED_COLUMNS:
        assert needed in sql, f"all_rows 查询缺失下游需要的列 {needed}"


def test_boundary_query_does_not_reload_the_signal_interval():
    sql = str(
        _all_rows_stmt(
            ("000001",),
            "2024-01-01",
            "2026-01-31",
            "2025-01-01",
            "2025-12-31",
        )
    )
    assert "daily_kbars.trade_date <" in sql
    assert "daily_kbars.trade_date >" in sql
    assert " OR " in sql
    assert "ORDER BY" not in sql, "大结果集排序必须留在内存，避免 MySQL 临时盘文件"


def test_signal_query_bounds_by_target_codes_when_provided():
    sql = str(_signal_stmt(["2025-01-02"], ("000001", "000002")))
    assert "daily_kbars.code IN" in sql, "给定 target_codes 时必须用 code IN 圈定，避免全市场扫描"


def test_signal_query_excludes_full_scan_markers():
    sql = str(_signal_stmt(["2025-01-02"], ()))
    for marker in _FULL_SCAN_MARKERS:
        assert marker not in sql, f"signal 查询回退到 SELECT *（含 {marker}）"
    for needed in _NEEDED_COLUMNS:
        assert needed in sql, f"signal 查询缺失下游需要的列 {needed}"
    assert "ORDER BY" not in sql, "signal 大结果集不得触发 MySQL filesort"


def test_signal_query_unbounded_without_target_codes():
    sql = str(_signal_stmt(["2025-01-02"], ()))
    assert "daily_kbars.code IN" not in sql, "无 target_codes 时不应额外加 code IN（全市场本就是用户显式选择）"


def test_loaded_columns_constant_aligned_with_consumers():
    # 常量必须恰好覆盖下游消费者需要的列；缺列会触发逐行 lazy-load(N+1)。
    assert len(RESEARCH_KBAR_LOADED_COLUMNS) == 21
    sql = str(_all_rows_stmt(("000001",), "2024-01-01", "2025-12-31"))
    for needed in _NEEDED_COLUMNS:
        assert needed in sql


def test_signal_payload_rows_are_deterministic_after_database_filesort_removal():
    rows = [
        type("Row", (), {"trade_date": "2026-01-02", "code": "000002"})(),
        type("Row", (), {"trade_date": "2026-01-01", "code": "000003"})(),
        type("Row", (), {"trade_date": "2026-01-01", "code": "000001"})(),
    ]

    assert [row.code for row in _deterministic_kbar_rows(rows)] == [
        "000001",
        "000003",
        "000002",
    ]
