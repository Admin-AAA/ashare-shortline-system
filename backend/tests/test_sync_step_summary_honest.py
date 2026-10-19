from app.services.scheduler_pure import _sync_step_summary


def test_summary_counts_batch_failures_and_lists_known_gaps():
    res = {
        "action_summary": {"completed_count": 1, "failed_count": 0},
        "batch_sync": {
            "margin_detail": {"status": "failed"},
            "news_announcement": {"status": "known_gap"},
            "block_trade": {"status": "success"},
        },
    }
    text = _sync_step_summary("辅助数据补数完成", res)
    assert "失败 1" in text and "margin_detail" in text and "已知缺口不计失败: news_announcement" in text


def test_summary_unchanged_without_batch_sync():
    assert _sync_step_summary("X", {"action_summary": {"completed_count": 2}}) == "X 2 / 失败 0"
