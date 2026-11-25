from types import SimpleNamespace

from sqlalchemy import create_engine, text

from app.services import scheduler_service as s


def _err(code: str):
    return SimpleNamespace(code=SimpleNamespace(value=code))


def test_snapshot_pointer_errors_with_mysql_fallback_are_degraded():
    assert s._batch_sync_item_status(
        "news_announcement", [_err("snapshot_pointer_missing")], fallback_covered=True) == "degraded"
    assert s._batch_sync_item_status(
        "news_announcement", [_err("snapshot_publish_blocked")], fallback_covered=True) == "degraded"
    # 非快照类错误照实计 failed，兜底行数不能掩盖取数/写入失败
    assert s._batch_sync_item_status(
        "news_announcement", [_err("fetch_failed")], fallback_covered=True) == "failed"
    assert s._batch_sync_item_status(
        "news_announcement", [_err("snapshot_pointer_missing")], fallback_covered=False) == "failed"


def test_aux_snapshot_fallback_covered_checks_mysql_floor():
    eng = create_engine("sqlite://")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE news_announcement (announce_date VARCHAR(10))"))
        for _ in range(40):
            conn.execute(text("INSERT INTO news_announcement VALUES ('2026-11-24')"))
    with eng.connect() as conn:
        assert s._aux_snapshot_fallback_covered(
            conn, "news_announcement", [_err("snapshot_pointer_missing")], "2026-11-24") is True
        assert s._aux_snapshot_fallback_covered(
            conn, "news_announcement", [_err("fetch_failed")], "2026-11-24") is False
        assert s._aux_snapshot_fallback_covered(
            conn, "news_announcement", [_err("snapshot_pointer_missing")], "2026-11-25") is False


def test_known_gap_datasets_do_not_count_as_failed():
    assert s._batch_sync_item_status("share_float", ["x"]) == "known_gap"
    assert s._batch_sync_item_status("margin_detail", ["x"]) == "failed"
    # 2026-10-19：news_announcement / holder_trade 已修复，移出已知缺口，失败如实计 failed
    assert s._batch_sync_item_status("news_announcement", ["x"]) == "failed"
    assert s._batch_sync_item_status("holder_trade", ["x"]) == "failed"
    assert s._batch_sync_item_status("news_announcement", []) == "success"


def test_holder_trade_no_longer_skipped_on_non_monday():
    ds = ("capital_flow", "holder_trade")
    assert s._skip_slow_known_gaps(ds, "2026-10-15") == (ds, [])
    assert s._skip_slow_known_gaps(ds, "2026-10-12") == (ds, [])
