"""serialize_run_summary 的 evaluation_scope 投影契约（L-051 回归防护）。

前端 RunModeBadge 靠 evaluation_scope 区分「实盘正式推荐」与「历史回放/补跑」：
REPLAY run 的 run_mode=formal 但 evaluation_scope=historical_replay，若后端不暴露该
字段，前端会把回放验证误显示为「实盘」。本测试锁定该字段的投影行为。
"""
from datetime import datetime
from types import SimpleNamespace

from app.services.recommendations.projection import serialize_run_summary


def _run(**overrides):
    base = dict(
        run_id="run-eval-001",
        run_name="回放验证",
        preset_id=None,
        trade_date="2026-06-01",
        run_mode="formal",
        started_at=datetime(2026, 6, 1, 9, 30),
        finished_at=datetime(2026, 6, 1, 9, 31),
        market_scope="all",
        market_scope_value=None,
        risk_template="balanced",
        score_line=72.0,
        universe_count=0,
        recommended_count=0,
        status="completed",
        error_message=None,
        heartbeat_at=datetime(2026, 6, 1, 9, 31),
        progress_phase="completed",
        progress_current=1,
        progress_total=1,
        progress_message="运行完成",
        interrupted_at=None,
        selected_skills_json="[]",
        result_summary_json="{}",
        checkpoint_phase="persist",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _summary(run):
    return serialize_run_summary(
        run,
        SimpleNamespace(name="预设"),
        hit_rate=0.0,
        active_statuses={"queued", "running"},
        heartbeat_stale_seconds=1200,
        now=datetime(2026, 6, 1, 9, 32),
    )


def test_evaluation_scope_historical_replay_projected():
    """REPLAY run（formal + historical_replay）必须暴露 historical_replay。"""
    summary = _summary(_run(evaluation_scope="historical_replay"))
    assert summary["evaluation_scope"] == "historical_replay"
    assert summary["run_mode"] == "formal"


def test_evaluation_scope_live_forward_projected():
    """正式推荐 run（formal + live_forward）必须暴露 live_forward。"""
    summary = _summary(_run(evaluation_scope="live_forward"))
    assert summary["evaluation_scope"] == "live_forward"


def test_evaluation_scope_defaults_to_historical_replay():
    """无 evaluation_scope 时按数据库 server_default 投影 historical_replay。"""
    summary = _summary(_run())
    assert summary["evaluation_scope"] == "historical_replay"
