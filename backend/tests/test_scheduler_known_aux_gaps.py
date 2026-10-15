from app.services import scheduler_service as s


def test_known_gap_datasets_do_not_count_as_failed():
    assert s._batch_sync_item_status("news_announcement", ["x"]) == "known_gap"
    assert s._batch_sync_item_status("share_float", ["x"]) == "known_gap"
    assert s._batch_sync_item_status("margin_detail", ["x"]) == "failed"
    assert s._batch_sync_item_status("news_announcement", []) == "success"
