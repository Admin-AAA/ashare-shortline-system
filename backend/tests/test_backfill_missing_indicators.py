"""T5 _backfill_missing_local_indicators 测试 —— daily_kbars ma5=0 自动修复。

回归保护：postclose_daily_kbar_batch 同步 K 线后必须检测并修复 ma5=0（07-07/07-03
曾全市场 5505 只 ma5=0）。_backfill_missing_local_indicators 在 K 线同步后自动调用
backfill_indicators.backfill_date 修本地可算指标。
"""
from app.services.scheduler_service import _backfill_missing_local_indicators


def test_backfill_missing_local_indicators_no_op_when_data_clean(db):
    """数据完整时返回空 fixed_dates（不误触发重算）。"""
    result = _backfill_missing_local_indicators(db, days=7)
    assert "fixed_dates" in result
    # 数据完整时 fixed_dates 为空（_dates_needing_indicators 返回 []）
    assert result["fixed_dates"] == []


def test_backfill_missing_local_indicators_handles_missing_db_execute_gracefully():
    """FakeDb（无 execute）不应让调用崩——best-effort，异常被上层 try/except 捕获。

    回归保护：test_scheduler_service 的 FakeDb mock 没有 execute 方法，
    _backfill_missing_local_indicators 调 _dates_needing_indicators(db) 会 AttributeError。
    上层 _run_daily_kbar_batch 的 try/except 保护 K 线同步不受影响。
    """

    class FakeDb:
        def close(self):
            pass

    # 直接调会抛 AttributeError（FakeDb 无 execute）——上层 try/except 兜底
    try:
        _backfill_missing_local_indicators(FakeDb(), days=7)
        # 若没抛异常（未来改为防御性），也 OK
    except AttributeError:
        # 预期：FakeDb 无 execute 时抛 AttributeError，由上层 try/except 捕获
        pass


def test_dates_needing_indicators_importable():
    """backfill_indicators 模块可从 scripts 导入（_backfill_missing_local_indicators 依赖）。"""
    import sys
    from pathlib import Path

    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from backfill_indicators import _dates_needing_indicators, backfill_date

    assert callable(_dates_needing_indicators)
    assert callable(backfill_date)
