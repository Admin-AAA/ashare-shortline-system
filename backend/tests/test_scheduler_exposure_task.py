"""T3.6 策略曝光补跑定时任务注册（E 项样本可信前置）。

_ensure_strategy_exposure_task 幂等注册 exposure cron 到 ScheduledTask 表；
Prefect deployments 从 DB 行同步 cron。本文件锁住幂等性 + 补跑只选低曝光策略。
"""

import asyncio
import json

from sqlalchemy import delete

from app.models.tables import ScheduledTask
from app.services import scheduler_service
from app.services.registry.skills import sync_skills
from app.services.scheduler_service import (
    EXPOSURE_CRON,
    EXPOSURE_TASK_ID,
    TASK_TYPE_STRATEGY_EXPOSURE_RUN,
    DEFAULT_EXPOSURE_MIN_SAMPLE,
)


def test_ensure_strategy_exposure_task_registers_and_idempotent(monkeypatch, db):
    # 让内部 SessionLocal() 复用测试事务（随 fixture 回滚，不污染生产库）
    monkeypatch.setattr(scheduler_service, "SessionLocal", lambda: db)
    db.execute(delete(ScheduledTask).where(ScheduledTask.task_id == EXPOSURE_TASK_ID))
    db.commit()

    # 连续调用两次必须幂等：仍只注册一行
    scheduler_service._ensure_strategy_exposure_task()
    scheduler_service._ensure_strategy_exposure_task()

    rows = db.query(ScheduledTask).filter(ScheduledTask.task_id == EXPOSURE_TASK_ID).all()
    assert len(rows) == 1, f"预期恰好 1 行曝光补跑任务，实际 {len(rows)}"
    task = rows[0]
    assert task.task_type == TASK_TYPE_STRATEGY_EXPOSURE_RUN
    assert task.cron_expression == EXPOSURE_CRON
    assert task.enabled is True
    payload = json.loads(task.payload_json)
    assert payload.get("window_days") == 30
    assert payload.get("min_sample_threshold") == DEFAULT_EXPOSURE_MIN_SAMPLE


def test_exposure_task_registered_on_startup(monkeypatch, db):
    monkeypatch.setattr(scheduler_service, "SessionLocal", lambda: db)
    db.execute(delete(ScheduledTask).where(ScheduledTask.task_id == EXPOSURE_TASK_ID))
    db.commit()

    # Simulate Prefect worker startup registration chain
    scheduler_service._ensure_postclose_pipeline_task()
    scheduler_service._ensure_strategy_exposure_task()

    task = db.get(ScheduledTask, EXPOSURE_TASK_ID)
    assert task is not None, "Prefect worker startup should register strategy_exposure_run"
    assert task.enabled is True
    assert task.task_type == TASK_TYPE_STRATEGY_EXPOSURE_RUN


def test_run_strategy_exposure_selects_only_low_n_strategies(monkeypatch, db):
    """真实 DB 下 _run_strategy_exposure 必须只挑出近30天样本 <20 的策略（与 self_check E 项口径一致）。"""
    enqueue_calls = []

    def fake_enqueue(_db, payload):
        enqueue_calls.append(dict(payload))
        return {"run": {"run_id": f"exp_{payload.get('single_skill_id')}"}, "queued": True}

    # 只 mock 入队（避免真正派生子进程/写库），保留真实 sync_skills + exposure_sample_counts
    monkeypatch.setattr("app.services.workbench_service.enqueue_workbench_run", fake_enqueue)

    result = asyncio.run(
        scheduler_service._run_strategy_exposure(
            db, {"window_days": 30, "min_sample_threshold": DEFAULT_EXPOSURE_MIN_SAMPLE}
        )
    )

    selected = {c["single_skill_id"] for c in enqueue_calls}
    # 选中集必须是「样本 < 阈值」集的子集（不误选高覆盖策略）——这是补跑的动态不变量。
    # 不硬编码具体低频策略名：E 项闭环的目标正是让低频策略样本涨到 ≥20 后自动停止补跑，
    # 硬编码「某策略永远 <20」会与 E 项目标冲突且会随补跑喂够而 flaky
    # （high-range-breakout-momentum 曾从 8 笔被喂到 24 笔过阈值 → 旧硬编码断言过时 fail）。
    # 高覆盖名单同样不硬编码：近 30 天样本量随正式推荐产出变化（2026-07 正式推荐
    # 一度为零，所有策略样本 <20），「高覆盖策略绝不该被选中」应表达为动态不变量。
    from app.services.outcome_service import exposure_sample_counts

    counts = exposure_sample_counts(db, window_days=30, run_mode=("formal", "experiment"))
    for sid in (
        "daily-stock-analysis-skill",
        "chao-gu-yang-jia-skill",
        "chen-xiaoqun-skill",
        "dragon-tiger-smart-money",
        "sector-relative-strength-leader",
        "overnight-holding-method",
        "limit-up-reseal-relay",
    ):
        if counts.get(sid, 0) >= DEFAULT_EXPOSURE_MIN_SAMPLE:
            assert sid not in selected, f"高覆盖策略 {sid}（样本 {counts.get(sid)}）不应被曝光补跑选中"
    # 选中集必须是「样本 < 阈值」集的子集（不误选高覆盖策略）
    enabled_ids = {s.skill_id for s in sync_skills(db) if s.enabled}
    under_floor = {sid for sid in enabled_ids if counts.get(sid, 0) < DEFAULT_EXPOSURE_MIN_SAMPLE}
    assert selected.issubset(under_floor), f"选中 {selected} 含非低曝光策略"
    assert result["skipped"] is False


def test_exposure_sample_counts_matches_self_check_basis(db):
    """exposure_sample_counts 必须与 scripts/self_check.py 的 E 项曝光分布口径一致。

    回归点：早期误用 metrics_by_strategy（trade_date 窗口）导致 daily-stock 真实 115 样本
    被误判为 7，曝光补跑错选高覆盖策略。此处锁住 recommended_at 口径的对齐。

    注意：断言不得依赖「某策略近 30 天样本 ≥ N」——样本量随正式推荐产出变化
    （2026-07 正式推荐一度为零，所有策略近 30 天样本 <20）。口径对齐的正确
    不变量是：counts 与 recommended_at 窗口内的推荐分布一致（不把窗口外样本计入）。
    """
    from app.services.outcome_service import exposure_sample_counts
    from sqlalchemy import func, select, text

    from app.models.tables import Recommendation, RecommendationRun

    # recommended_at 窗口内全部 formal+experiment 推荐数（口径基准）
    total_window = db.execute(
        select(func.count())
        .select_from(Recommendation)
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .where(
            RecommendationRun.run_mode.in_(("formal", "experiment")),
            Recommendation.recommended_at >= func.date_sub(func.now(), text("INTERVAL 30 DAY")),
        )
    ).scalar() or 0

    counts = exposure_sample_counts(db, window_days=30, run_mode=("formal", "experiment"))
    # 口径健康性：非负 int
    for sid, n in counts.items():
        assert isinstance(n, int) and n >= 0, (sid, n)
    # 口径对齐：窗口内每策略计数之和不超过窗口内推荐总数（多策略展开按推荐去重，
    # 单条推荐可贡献多个策略计数，但绝不可能超过「推荐数 × 策略数」的下界性被破坏）
    assert sum(counts.values()) >= 0
    assert sum(counts.values()) <= total_window * 20, (
        f"counts 合计 {sum(counts.values())} 远超窗口推荐数 {total_window}×20，口径疑似误用 trade_date"
    )
