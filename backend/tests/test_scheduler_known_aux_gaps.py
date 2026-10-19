from app.services import scheduler_service as s


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
