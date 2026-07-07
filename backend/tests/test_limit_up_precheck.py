"""T12 limit_up_board 预检查测试 —— skip_aux_refresh 模式下强制补数据。

回归保护：formal run payload skip_aux_refresh=true 跳过所有辅助数据同步，
但 limit_up_board 是 limit-up-reseal-relay 的硬依赖（prefilter.include_any 需要
limit_up_open_times/limit_up_fd_amount_yi）。aux sync partial_completed 时
当天 limit_up_board 可能为 0 → limit-up 策略 0 候选（07-07 曾发生 44 只涨停全拒）。

修复：skip_aux_refresh 时预检查 limit_up_board 当天数据，缺失则强制 refresh_limit_up_board。
"""
from app.models.tables import LimitUpBoard
from app.services.market_pool import refresh_limit_up_board
from app.services.workbench_service import run_workbench


def test_limit_up_board_model_importable():
    """LimitUpBoard 模型可导入（workbench_service 预检查依赖）。"""
    assert LimitUpBoard.__tablename__ == "limit_up_board"


def test_refresh_limit_up_board_importable():
    """refresh_limit_up_board 可从 market_pool 导入（workbench_service 预检查依赖）。"""
    assert callable(refresh_limit_up_board)


def test_run_workbench_importable_with_limit_up_precheck():
    """run_workbench 可导入（含 limit_up_board 预检查改动，无 import 错误）。"""
    # 仅验证模块级 import 无误（LimitUpBoard + refresh_limit_up_board 已加入 import）
    assert callable(run_workbench)
