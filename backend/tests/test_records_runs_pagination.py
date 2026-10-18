from app.api import records as rec


def _rows():
    return [
        {"run_id": f"r{i}", "run_mode": "formal" if i % 2 else "experiment", "status": "completed",
         "trade_date": f"2026-10-{10 + i:02d}", "preset_id": "p", "result_summary": {"big": i}}
        for i in range(6)
    ][::-1]


def test_legacy_shape_unchanged(monkeypatch):
    monkeypatch.setattr(rec, "list_run_records", lambda db, limit=100: _rows()[:limit])
    assert rec.run_records(limit=3, page=None, page_size=None, run_mode=None, status=None,
                           trade_date_from=None, trade_date_to=None, preset_id=None, fields=None, db=None) == _rows()[:3]


def test_paged_filtered_total_and_slim(monkeypatch):
    monkeypatch.setattr(rec, "list_run_records", lambda db, limit=100: _rows()[:limit])
    out = rec.run_records(limit=100, page=1, page_size=2, run_mode="formal", status=None,
                          trade_date_from=None, trade_date_to=None, preset_id=None, fields="slim", db=None)
    assert out["total"] == 3 and out["page"] == 1 and out["page_size"] == 2
    assert [r["run_id"] for r in out["items"]] == ["r5", "r3"]
    assert all("result_summary" not in r for r in out["items"])
