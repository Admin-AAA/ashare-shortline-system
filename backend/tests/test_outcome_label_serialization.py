"""label_pending_outcomes 并发串行锁测试。

背景（D 项前瞻验证前置基础设施）：三个入口（startup 补结算线程 / 首屏预热
_auto_backfill_outcomes / cron 18:20 _run_outcome_label）都直接调
label_pending_outcomes，各自用独立 SessionLocal。若不串行，两个 session 在各自
事务隔离下都把同一批 pending rec 当"未结算"捞出（看不到对方未提交的 INSERT），
第二个 db.commit() 的 executemany 撞 PRIMARY KEY 1062 → 整批回滚、0 笔结算。
app.log 2026-06-23 样本已实证（rec_20260623_001_0fcd 重复 INSERT 致 startup
补结算静默失败，7/9 的 28 条前瞻样本长期 0 结算）。

本测试验证 _label_outcome_lock 串行化后：
1. 两个并发线程不重叠执行 label_pending_outcomes 临界区。
2. 并发调用后只产生 1 条 outcome（不撞主键、不重复 INSERT）。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

from app.models.tables import (
    DailyKbar,
    Recommendation,
    RecommendationOutcome,
    RecommendationRun,
    Stock,
)
from app.services import outcome_service
from app.services.outcome_service import label_pending_outcomes


def _unique_stock_code(prefix: str = "98") -> str:
    return f"{prefix}{uuid.uuid4().int % 10000:04d}"


def _make_pending_rec(db: Session, rec_id: str, code: str, trade_date: str = "2026-05-12") -> str:
    """构造一条 pending 推荐 + 足够 horizon 的 K 线，供 label_pending_outcomes 结算。"""
    run_id = f"run_lock_{uuid.uuid4().hex[:8]}"
    db.add(RecommendationRun(
        run_id=run_id, run_name="并发锁测试", trade_date=trade_date,
        market_scope="all", risk_template="balanced", output_count=1,
        universe_count=1, recommended_count=1, selected_skills_json="[]",
        params_json="{}", market_snapshot_json="{}", result_summary_json="{}",
        status="completed", run_mode="formal",
    ))
    db.flush()
    db.add(Stock(code=code, name="并发锁测试股", market="创业板", industry="电子", is_st=False))
    db.add_all([
        DailyKbar(code=code, trade_date=trade_date, open=10.0, high=10.2, low=9.9, close=10.0, volume=1, amount=10, pct_chg=0.0, turnover_rate=1.0),
        DailyKbar(code=code, trade_date="2026-05-13", open=10.1, high=10.6, low=9.95, close=10.5, volume=1, amount=10, pct_chg=5.0, turnover_rate=1.2),
        DailyKbar(code=code, trade_date="2026-05-14", open=10.6, high=11.0, low=10.45, close=10.9, volume=1, amount=10, pct_chg=3.8, turnover_rate=1.3),
        # 2026-09-15（execution_policy v3）：止盈已关闭 → 需要一根明确击穿 profit_floor 的 K
        # 才能得到 settled（success）而不是 provisional；否则持仓期未跑完会停在临时态。
        DailyKbar(code=code, trade_date="2026-05-15", open=10.8, high=10.9, low=10.2, close=10.3, volume=1, amount=10, pct_chg=-5.5, turnover_rate=1.1),
        DailyKbar(code=code, trade_date="2026-05-18", open=10.3, high=10.4, low=10.1, close=10.2, volume=1, amount=10, pct_chg=-1.0, turnover_rate=1.0),
        DailyKbar(code=code, trade_date="2026-05-19", open=10.2, high=10.3, low=10.0, close=10.1, volume=1, amount=10, pct_chg=-1.0, turnover_rate=1.0),
    ])
    db.add(Recommendation(
        recommendation_id=rec_id, run_id=run_id, trade_date=trade_date,
        code=code, name="并发锁测试股", industry="电子", recommend_price=10.0,
        bucket="core", final_score=82.0, consensus_score=80.0, risk_tags_json="[]",
        feature_snapshot_json=json.dumps({
            "ideal_buy": 10.0, "secondary_buy": 10.15,
            "stop_loss": 9.6, "take_profit": 10.8,
            "market_discipline": {"position_label": "中仓", "max_position_pct": 55},
        }),
        market_context_json="{}", skill_breakdown_json="[]", agent_summary_json="{}",
        status="pending", recent_repeat=False,
    ))
    db.commit()
    return run_id


@pytest.fixture
def committed_pending_rec():
    """提交一组独立 Session 可见的结算样本，并按精确主键清理。"""
    from app.db.session import SessionLocal

    suffix = uuid.uuid4().hex[:8]
    rec_id = f"rec_lock_dup_{suffix}"
    code = _unique_stock_code("97")
    setup_db = SessionLocal()
    try:
        run_id = _make_pending_rec(setup_db, rec_id, code)
    finally:
        setup_db.close()

    try:
        yield rec_id, code, run_id
    finally:
        cleanup_db = SessionLocal()
        try:
            cleanup_db.query(RecommendationOutcome).filter(
                RecommendationOutcome.recommendation_id == rec_id
            ).delete(synchronize_session=False)
            cleanup_db.query(Recommendation).filter(
                Recommendation.recommendation_id == rec_id
            ).delete(synchronize_session=False)
            cleanup_db.query(DailyKbar).filter(
                DailyKbar.code == code
            ).delete(synchronize_session=False)
            cleanup_db.query(Stock).filter(
                Stock.code == code
            ).delete(synchronize_session=False)
            cleanup_db.query(RecommendationRun).filter(
                RecommendationRun.run_id == run_id
            ).delete(synchronize_session=False)
            cleanup_db.commit()
        finally:
            cleanup_db.close()


def test_label_pending_outcomes_is_serialized_by_lock(committed_pending_rec):
    """_label_outcome_lock 保证 label_pending_outcomes 临界区不重叠。

    mock _simulate_outcome 注入 sleep，记录两个线程是否同时进入临界区。
    修复前（无锁）：重叠计数 > 0；修复后（有锁）：重叠计数 == 0。
    """
    from app.db.session import SessionLocal

    active = 0
    max_active = 0
    state_lock = threading.Lock()

    def _spy_simulate(rec, bars, dates, horizon_days, feature_snapshot=None, **_kwargs):
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.15)  # 拉长临界区，确保线程重叠窗口存在
        with state_lock:
            active -= 1
        # 返回 None 表示跳过（不写 outcome），本测试只验证串行性，不验证结算
        return None

    results: dict[str, object] = {}

    def worker(name):
        db = SessionLocal()
        try:
            r = label_pending_outcomes(db, horizon_days=5)
            results[name] = r
        except Exception as e:  # noqa: BLE001
            results[name] = ("err", str(e)[:120])
        finally:
            db.close()

    with patch.object(outcome_service, "_simulate_outcome", side_effect=_spy_simulate):
        # 查询会捞出当前所有 pending（含真实数据），但 _spy_simulate 返回 None 不写库
        t1 = threading.Thread(target=worker, args=("A",))
        t2 = threading.Thread(target=worker, args=("B",))
        t1.start(); t2.start()
        t1.join(); t2.join()

    assert max_active == 1, (
        f"label_pending_outcomes 临界区未串行：最大并发 {max_active}（应为 1）。"
        " 多入口并发会撞 PRIMARY KEY 1062 致整批回滚。"
    )


def test_concurrent_label_pending_outcomes_does_not_duplicate(
    db: Session,
    committed_pending_rec,
):
    """两个独立 session 并发结算同一条 pending rec，不撞主键、只产生 1 条 outcome。

    复现 app.log 2026-06-23 场景：startup 补结算线程与首屏预热 backfill 同时跑。
    修复前：第二个 commit 撞 1062 → IntegrityError；修复后：串行，第二次 db.get 命中 update。

    注意：db fixture 用 savepoint 模式（join_transaction_mode=create_savepoint），
    其插入的数据对独立 SessionLocal 不可见（未真正 commit）。故此处用独立
    SessionLocal 真正 commit 插入测试数据，测完手动清理，避免污染生产库。
    """
    from app.db.session import SessionLocal

    rec_id, _code, _run_id = committed_pending_rec

    errors: list[str] = []
    results: dict[str, object] = {}

    def worker(name):
        sdb = SessionLocal()
        try:
            r = label_pending_outcomes(sdb, horizon_days=5)
            results[name] = r
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
        finally:
            sdb.close()

    t1 = threading.Thread(target=worker, args=("A",))
    t2 = threading.Thread(target=worker, args=("B",))
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert not errors, f"并发结算抛异常（应被串行锁消除）: {errors}"

    verify_db = SessionLocal()
    try:
        outcome = verify_db.get(RecommendationOutcome, rec_id)
        assert outcome is not None, (
            f"并发结算后应已结算。results={results} errors={errors}"
        )
        assert outcome.entry_triggered is True
        assert outcome.outcome_label == "success"
        # 只产生 1 条 outcome（不重复 INSERT）——主键约束天然保证，串行后第二次走 update
    finally:
        verify_db.close()
