"""T4: /api/stats/by-preset 冷调用 4.5s+ 回归测试。

根因：_runs_for_mode 全量加载 RecommendationRun 实体（含 4 个巨型 JSON 列，
每条 run avg ~2.7MB），890 条 run 产生 ~2.4GB 传输。改为 load_only 后
get_preset_stats 不再加载 market_snapshot_json，冷调用 <0.2s（~50x）。

验证：
1. _runs_for_mode 默认不加载巨列（market_snapshot_json 延迟加载），
   但 run_id/preset_id/finished_at/selected_skills_json 已加载。
2. _get_preset_stats / _get_skill_stats 聚合结构与 regime 路径正确（与旧逻辑等价）。
"""
from __future__ import annotations

import pytest
from sqlalchemy import inspect

from app.db.session import SessionLocal
from app.services import stats_service as ss
from app.services.stats_service import RecommendationRun


def _db():
    try:
        db = SessionLocal()
        db.execute(__import__("sqlalchemy").text("SELECT 1")).fetchone()
        return db
    except Exception:
        pytest.skip("MySQL 不可达，跳过 stats_service 集成测试")


def test_runs_for_mode_defers_heavy_json_column():
    db = _db()
    try:
        runs = ss._runs_for_mode(db)
        assert runs, "应有 run 数据"
        # 需要的列已加载
        assert all(run.run_id for run in runs)
        # market_snapshot_json 未被 load_only 加载（在 unloaded 集合中 = 延迟）
        heavy_unloaded = ["market_snapshot_json" in inspect(run).unloaded for run in runs[:50]]
        assert all(heavy_unloaded), "market_snapshot_json 应被 load_only 延迟，不应随 run 实体物化"
    finally:
        db.close()


def test_runs_for_mode_skill_optin_loads_market_snapshot():
    db = _db()
    try:
        runs = ss._runs_for_mode(db, extra_cols=[RecommendationRun.market_snapshot_json])
        assert runs
        # get_skill_stats 需要 regime（埋在 market_snapshot_json），显式 opt-in 后应已加载
        assert "market_snapshot_json" not in inspect(runs[0]).unloaded, \
            "opt-in 后 market_snapshot_json 应已加载（skill regime 路径依赖）"
    finally:
        db.close()


def test_preset_stats_structure_and_integrity():
    db = _db()
    try:
        presets = ss._get_preset_stats(db)
        assert isinstance(presets, list) and presets
        keys = {"preset_id", "preset_name", "run_count", "recommendation_count",
                "hit_rate", "avg_score", "last_run_at"}
        for p in presets:
            assert keys.issubset(p.keys())
        # 临时运行 bucket 必须存在（adhoc runs 归并）
        names = {p["preset_name"] for p in presets}
        assert "临时运行" in names
        # 产品统计与 metrics overview 共用正式实盘 + (trade_date, code) 去重口径。
        from app.services.outcome_service import overview_metrics
        total = overview_metrics(db)["total_recommendations"]
        s = sum(p["recommendation_count"] for p in presets)
        assert s == total, f"preset 推荐数之和 {s} 应等于正式实盘 recommendations 行数 {total}"
    finally:
        db.close()


def test_skill_stats_regime_path_populated():
    db = _db()
    try:
        skills = ss._get_skill_stats(db)
        assert isinstance(skills, list) and skills
        for s in skills:
            assert "top_market_regimes" in s
            assert isinstance(s["top_market_regimes"], list)
        # 至少应有策略带非空 regime 分布（证明 market_snapshot_json 被正确消费）
        assert any(s["top_market_regimes"] for s in skills)
    finally:
        db.close()
