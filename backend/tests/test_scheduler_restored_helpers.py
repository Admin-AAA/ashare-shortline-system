import typing

from app.services import scheduler_service as s


def test_default_sync_scope():
    assert s._default_sync_scope("data_backfill") == "daily_bars"
    assert s._default_sync_scope("pool_refresh") == "quotes"
    assert s._default_sync_scope("data_sync") == "all"


def test_parse_backfill_stdout():
    out = "noise\nscope=all universe=5000 ready_or_progress=4990 pending=10\nprocessed=10 persisted=9 short_history=1 failed=0 elapsed=3.5s\n"
    r = s._parse_backfill_stdout(out)
    assert (r["scope"], r["universe"], r["pending"]) == ("all", 5000, 10)
    assert (r["processed"], r["persisted"], r["failed"], r["elapsed_seconds"]) == (10, 9, 0, 3.5)
    assert r["summary_text"] == "处理 10，入库 9，短历史 1，失败 0"
    assert s._parse_backfill_stdout("")["summary_text"] == "日K回填完成"


def test_skills_annotations_resolve():
    from app.services.registry import skills
    from app.engine.strategy import StrategyDescriptor
    for fn in (skills.skill_version_id, skills._descriptor_map, skills._strategy_snapshot):
        typing.get_type_hints(fn, localns={"StrategyDescriptor": StrategyDescriptor})


def test_strategy_profile_all_resolves():
    from app.services.backtest import strategy_profile as sp
    for name in sp.__all__:
        assert hasattr(sp, name), name
