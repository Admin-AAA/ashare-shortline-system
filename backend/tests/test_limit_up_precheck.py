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


def test_inject_limit_up_flags_defaults_twenty_cm_risk_gate_for_non_limit_up(monkeypatch):
    """非涨停股注入 twenty_cm_risk_gate 默认值，防 strict_missing prefilter 死锁。

    回归保护（第146轮）：strict_missing 修复使 `twenty_cm_risk_gate != 'block'` 在
    字段缺失时返回 False，但非涨停股在 prefilter 阶段没有该字段（feature_builder 在
    prefilter 之后才重算），导致 overnight/sector 等策略拒绝全部非涨停股候选。
    inject_limit_up_flags 给所有股票补 `twenty_cm_risk_gate='not_applicable'`，
    语义=未做20cm检测=不阻断（与 shortline_factors 一致）。
    """
    from app.services.market_pool import inject_limit_up_flags

    stocks = [
        {"code": "600000", "name": "非涨停"},  # 不在 limit_up_board
        {"code": "000001", "name": "涨停", "limit_times": 1, "open_times": 0, "fd_amount_yi": 0.5, "turnover_ratio": 4.0},
    ]

    # monkeypatch _load_cached_limit_up_board 让 000001 命中、600000 不命中
    import app.services.market_pool as mp

    def fake_load(db, trade_date=None):
        return [{"code": "000001", "limit_times": 1, "open_times": 0, "fd_amount_yi": 0.5, "turnover_ratio": 4.0}]
    monkeypatch.setattr(mp, "_load_cached_limit_up_board", fake_load)
    monkeypatch.setattr(mp, "_resolve_snapshot_trade_date", lambda db, trade_date=None: trade_date or "2026-07-09")

    class _FakeDB:
        pass
    hit = inject_limit_up_flags(_FakeDB(), stocks, trade_date="2026-07-09")
    assert hit == 1

    by_code = {s["code"]: s for s in stocks}
    # 涨停股（000001）走 if 分支，twenty_cm_risk_gate 由 setdefault 补 pass（涨停本就不阻断）
    assert by_code["000001"]["is_limit_up"] is True
    assert "twenty_cm_risk_gate" in by_code["000001"]
    # 非涨停股（600000）走 else 分支，必须有 twenty_cm_risk_gate 默认值（否则 strict_missing 死锁）
    assert by_code["600000"].get("is_limit_up") is False
    gate = by_code["600000"].get("twenty_cm_risk_gate")
    assert gate is not None, "非涨停股必须有 twenty_cm_risk_gate 默认值，否则 prefilter strict_missing 死锁"
    assert gate != "block", "默认值不能是 block（否则非涨停股全被误拒）"
    assert by_code["600000"]["limit_up_consecutive_days"] == 0
