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
