"""T7: 驾驶舱落地页推荐必须优先展示 formal 运行（北极星 A 项一致性）。

回归防护：_select_dashboard_recommendation_run 旧逻辑按 finished_at 排序、不区分
run_mode，导致 experiment run 完成更晚的交易日(07-07/07-03)落地页展示 experiment
推荐而非官方 formal 推送。修复后：formal 优先，无 formal 才回退到任意 completed run。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.api import dashboard as dashboard_mod


def _fake_run(run_id: str, run_mode: str, finished_at: str) -> SimpleNamespace:
    return SimpleNamespace(
        run_id=run_id,
        run_mode=run_mode,
        status="completed",
        trade_date="2026-07-07",
        finished_at=finished_at,
        started_at=finished_at,
    )


def test_select_prefers_formal_over_later_experiment(monkeypatch):
    """同一交易日 formal 02:35 与 experiment 02:42（更晚），必须选 formal。"""
    monkeypatch.setattr(dashboard_mod, "_latest_dashboard_trade_date", lambda db: "2026-07-07")
    formal = _fake_run("run_formal", "formal", "2026-07-07 02:35:44")
    experiment = _fake_run("run_exp", "experiment", "2026-07-07 02:42:47")
    db = MagicMock()
    # 第 1 次 scalar = formal 查询命中；第 2 次 scalar = 回退查询（不应被用到）
    db.scalar = MagicMock(side_effect=[formal, experiment])

    result = dashboard_mod._select_dashboard_recommendation_run(db)

    assert result is formal, "应优先返回 formal run，而非更晚完成的 experiment"
    assert db.scalar.call_count == 1, "formal 命中后不应再发起回退查询"


def test_fallback_to_any_completed_when_no_formal(monkeypatch):
    """该交易日无 formal 完成时才回退到最新的 completed run（experiment）。"""
    monkeypatch.setattr(dashboard_mod, "_latest_dashboard_trade_date", lambda db: "2026-07-07")
    experiment = _fake_run("run_exp", "experiment", "2026-07-07 02:42:47")
    db = MagicMock()
    db.scalar = MagicMock(side_effect=[None, experiment])

    result = dashboard_mod._select_dashboard_recommendation_run(db)

    assert result is experiment, "无 formal 时应回退到最新的 completed run"
    assert db.scalar.call_count == 2


def test_returns_none_when_no_completed_run(monkeypatch):
    monkeypatch.setattr(dashboard_mod, "_latest_dashboard_trade_date", lambda db: "2026-07-07")
    db = MagicMock()
    db.scalar = MagicMock(side_effect=[None, None, None])

    assert dashboard_mod._select_dashboard_recommendation_run(db) is None
    assert db.scalar.call_count == 3
