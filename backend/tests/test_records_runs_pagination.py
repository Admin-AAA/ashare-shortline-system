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
                           trade_date_from=None, trade_date_to=None, preset_id=None, fields=None, min_picks=None, sort="started_at", order="desc", db=None) == _rows()[:3]


def test_paged_filtered_total_and_slim(monkeypatch):
    monkeypatch.setattr(rec, "list_run_records", lambda db, limit=100: _rows()[:limit])
    out = rec.run_records(limit=100, page=1, page_size=2, run_mode="formal", status=None,
                          trade_date_from=None, trade_date_to=None, preset_id=None, fields="slim", min_picks=None, sort="started_at", order="desc", db=None)
    assert out["total"] == 3 and out["page"] == 1 and out["page_size"] == 2
    assert [r["run_id"] for r in out["items"]] == ["r5", "r3"]
    assert all("result_summary" not in r for r in out["items"])


def _call(**kw):
    base = dict(limit=100, page=1, page_size=10, run_mode=None, status=None, trade_date_from=None,
                trade_date_to=None, preset_id=None, fields=None, min_picks=None, sort="started_at", order="desc", db=None)
    base.update(kw)
    return rec.run_records(**base)


def _rows2():
    return [
        {"run_id": "a", "status": "completed", "recommended_count": 3, "started_at": "2026-10-18T09:00:00", "finished_at": "2026-10-18T09:10:00"},
        {"run_id": "b", "status": "failed", "recommended_count": 0, "started_at": "2026-10-18T10:00:00", "finished_at": None},
        {"run_id": "c", "status": "completed", "recommended_count": 5, "started_at": "2026-10-17T09:00:00", "finished_at": "2026-10-17T09:01:00"},
    ]


def test_min_picks_and_sorts(monkeypatch):
    monkeypatch.setattr(rec, "list_run_records", lambda db, limit=100: _rows2())
    out = _call(min_picks=1)
    assert out["total"] == 2 and [r["run_id"] for r in out["items"]] == ["a", "c"]
    assert [r["run_id"] for r in _call(sort="picks", order="desc")["items"]] == ["c", "a", "b"]
    assert [r["run_id"] for r in _call(sort="duration", order="asc")["items"]] == ["c", "a", "b"]
    assert [r["run_id"] for r in _call(sort="duration", order="desc")["items"]] == ["a", "c", "b"]
    assert [r["run_id"] for r in _call(sort="started_at", order="asc")["items"]] == ["c", "a", "b"]


def test_invalid_sort_is_422():
    from fastapi.testclient import TestClient
    from app.main import app

    c = TestClient(app)
    assert c.get("/api/records/runs?page=1&sort=bogus").status_code == 422
    assert c.get("/api/records/runs?page=1&order=up").status_code == 422
