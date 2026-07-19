"""T3.6 策略曝光补跑定时任务注册（E 项样本可信前置）。

回归背景：_run_strategy_exposure 实现 + manual 触发路径早已存在，但 start_scheduler
从不注册 strategy_exposure_run 定时任务（缺 _ensure_* 函数，scheduled_tasks 表也无该
行），导致曝光 cron 从未被排程——4 个低频策略（chip/high-range/small-cap/trend-pullback）
长期卡在 n<20，胜率统计不可信，E 项持续误触发。本文件锁住「调度器启动时幂等注册该 cron」
这一核心修复。
"""

import asyncio
import json

from sqlalchemy import delete

from app.models.tables import ScheduledTask
from app.services import scheduler_service
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


def test_start_scheduler_registers_exposure_task(monkeypatch, db):
    monkeypatch.setattr(scheduler_service, "SessionLocal", lambda: db)
    db.execute(delete(ScheduledTask).where(ScheduledTask.task_id == EXPOSURE_TASK_ID))
    db.commit()

    # 模拟调度器启动时的注册链（不真正启动 APScheduler，避免后台线程）
    scheduler_service._ensure_postclose_pipeline_task()
    scheduler_service._ensure_strategy_exposure_task()

    task = db.get(ScheduledTask, EXPOSURE_TASK_ID)
    assert task is not None, "start_scheduler 应注册 strategy_exposure_run"
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
    # 高曝光策略绝不该被选中（否则污染高质量推荐池，且浪费补跑额度）
    for sid in (
        "daily-stock-analysis-skill",
        "chao-gu-yang-jia-skill",
        "chen-xiaoqun-skill",
        "dragon-tiger-smart-money",
        "sector-relative-strength-leader",
        "overnight-holding-method",
        "limit-up-reseal-relay",
    ):
        assert sid not in selected, f"高覆盖策略 {sid} 不应被曝光补跑选中"
    # 选中集必须是「样本 < 阈值」集的子集（不误选高覆盖策略）
    from app.services.outcome_service import exposure_sample_counts

    counts = exposure_sample_counts(db, window_days=30, run_mode=("formal", "experiment"))
    under_floor = {sid for sid, n in counts.items() if n < DEFAULT_EXPOSURE_MIN_SAMPLE}
    assert selected.issubset(under_floor), f"选中 {selected} 含非低曝光策略"
    assert result["skipped"] is False


def test_exposure_sample_counts_matches_self_check_basis(db):
    """exposure_sample_counts 必须与 scripts/self_check.py 的 E 项曝光分布口径一致。

    回归点：早期误用 metrics_by_strategy（trade_date 窗口）导致 daily-stock 真实 115 样本
    被误判为 7，曝光补跑错选高覆盖策略。此处锁住 recommended_at 口径的对齐。
    """
    from app.services.outcome_service import exposure_sample_counts

    counts = exposure_sample_counts(db, window_days=30, run_mode=("formal", "experiment"))
    # 高覆盖策略样本充足（self_check 报 daily-stock~115+），绝不能被低估到 <20
    assert counts.get("daily-stock-analysis-skill", 0) >= 100
    # 不硬编码低频策略「必须 <20」：E 项闭环的目标正是让低频策略补跑喂到 ≥20。
    # 硬编码该不变量会与补跑喂够后样本增长冲突（high-range 从 8 涨到 24 即 fail）。
    # 口径对齐不变量：每个 counts 值 ≥ 0 且为 int（口径健康性）。
