"""版本化读缓存：数据版本指纹必须随库里任何增删改变化（跨进程可靠），缓存有上限、有兜底过期。"""
from __future__ import annotations

import threading
import time

import pytest

from app.models.market import DailyKbar
from app.models.strategy import Preset
from app.services.data_version import VersionedCache, data_version


def test_cache_recomputes_when_version_changes():
    cache = VersionedCache("t", maxsize=4, max_age=60)
    calls = []
    f = lambda: calls.append(1) or len(calls)  # noqa: E731
    assert cache.get_or_compute("k", "v1", f) == 1
    assert cache.get_or_compute("k", "v1", f) == 1
    assert cache.get_or_compute("k", "v2", f) == 2
    assert len(calls) == 2


def test_cache_max_age_and_lru_bound():
    cache = VersionedCache("t", maxsize=2, max_age=0.05)
    cache.get_or_compute("a", 1, lambda: "a")
    time.sleep(0.06)
    assert cache.get_or_compute("a", 1, lambda: "a2") == "a2"  # 兜底过期
    cache.get_or_compute("b", 1, lambda: "b")
    cache.get_or_compute("c", 1, lambda: "c")
    assert len(cache) == 2  # 超上限按 LRU 淘汰


def test_cache_single_flight_per_key():
    cache = VersionedCache("t", maxsize=4, max_age=60)
    calls = []

    def slow():
        calls.append(1)
        time.sleep(0.1)
        return "x"

    ths = [threading.Thread(target=cache.get_or_compute, args=("k", 1, slow)) for _ in range(5)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert len(calls) == 1


@pytest.mark.integration
def test_fingerprint_sees_insert_update_and_null(db):
    v0 = data_version(db, ["presets"])
    db.add(Preset(preset_id="zz_perf_probe", name="probe"))
    db.flush()
    v1 = data_version(db, ["presets"])
    assert v1 != v0  # 新增
    p = db.get(Preset, "zz_perf_probe")
    p.description = ""
    db.flush()
    v2 = data_version(db, ["presets"])
    assert v2 != v1  # NULL -> 空串 也要能发现
    p.name = "probe2"
    db.flush()
    assert data_version(db, ["presets"]) != v2  # 原地改写


@pytest.mark.integration
def test_fingerprint_sees_new_daily_bar(db):
    v0 = data_version(db, ["daily_kbars"])
    db.add(DailyKbar(code="999998", trade_date="2099-01-01", open=1, high=1, low=1, close=1))
    db.flush()
    assert data_version(db, ["daily_kbars"]) != v0


# ---- 迁移 0028：运行表 / 推荐表用 MySQL 自动维护的 updated_at 做指纹 ----

def _run(run_id: str):
    from app.models.tables import RecommendationRun

    return RecommendationRun(
        run_id=run_id, run_name="指纹测试", trade_date="2026-05-12", market_scope="all",
        risk_template="balanced", output_count=1, universe_count=1, recommended_count=0,
        selected_skills_json="[]", params_json="{}", market_snapshot_json="{}",
        result_summary_json="{}", status="completed", run_mode="formal",
    )


def _needs_row_version(db):
    from app.services import data_version as dv

    if db.get_bind().dialect.name != "mysql" or dv._row_version_sql(db, "recommendation_runs") is None:
        pytest.skip("测试库未迁移到 0028（没有 updated_at）")


@pytest.mark.integration
def test_runs_fingerprint_uses_row_version_index_only(db):
    from app.services import data_version as dv

    _needs_row_version(db)
    sql = dv._fingerprint_sql(db, "recommendation_runs")
    assert "FORCE INDEX (`ix_recommendation_runs_updated_at`)" in sql
    assert "result_summary_json" not in sql and "selected_skills_json" not in sql
    assert "FORCE INDEX (`ix_recommendations_updated_at`)" in dv._fingerprint_sql(db, "recommendations")


@pytest.mark.integration
def test_row_version_sees_orm_raw_sql_core_update_bulk_insert_and_delete(db):
    from sqlalchemy import insert, text as sql_text, update

    from app.models.tables import RecommendationRun

    _needs_row_version(db)
    t = ["recommendation_runs"]
    v0 = data_version(db, t)
    db.add(_run("zz_dv_probe_1"))
    db.flush()
    v1 = data_version(db, t)
    assert v1 != v0  # ORM 新增
    # 只改大字段（指纹不读它），updated_at 必须由 MySQL 刷新
    time.sleep(0.002)
    db.execute(sql_text("UPDATE recommendation_runs SET result_summary_json=:j WHERE run_id='zz_dv_probe_1'"),
               {"j": '{"x": 1}'})
    v2 = data_version(db, t)
    assert v2 != v1  # 原生 SQL 改大字段
    time.sleep(0.002)
    db.execute(update(RecommendationRun).where(RecommendationRun.run_id == "zz_dv_probe_1").values(status="failed"))
    v3 = data_version(db, t)
    assert v3 != v2  # core update()
    base = {k: getattr(_run("x"), k) for k in (
        "run_name", "trade_date", "market_scope", "risk_template", "output_count", "universe_count",
        "recommended_count", "selected_skills_json", "params_json", "market_snapshot_json",
        "result_summary_json", "status", "run_mode")}
    db.execute(  # core insert + 多行参数 = executemany，不经 ORM 实例
        insert(RecommendationRun.__table__),
        [{**base, "run_id": "zz_dv_probe_2"}, {**base, "run_id": "zz_dv_probe_3"}],
    )
    v4 = data_version(db, t)
    assert v4 != v3  # executemany 原生插入
    db.execute(sql_text("DELETE FROM recommendation_runs WHERE run_id='zz_dv_probe_3'"))
    assert data_version(db, t) != v4  # 删行


@pytest.mark.integration
def test_no_op_update_keeps_fingerprint(db):
    from sqlalchemy import text as sql_text

    _needs_row_version(db)
    db.add(_run("zz_dv_probe_4"))
    db.flush()
    v = data_version(db, ["recommendation_runs"])
    db.execute(sql_text("UPDATE recommendation_runs SET status=status WHERE run_id='zz_dv_probe_4'"))
    assert data_version(db, ["recommendation_runs"]) == v  # 值没变就不算改动（MySQL 不刷新 ON UPDATE 列）
