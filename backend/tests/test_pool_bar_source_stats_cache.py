import json

import app.services.data_sources as ds


def test_same_bar_date_reads_precomputed_without_query(tmp_path, monkeypatch):
    path = tmp_path / "stats.json"
    monkeypatch.setattr(ds, "_bar_source_stats_path", lambda: path)
    monkeypatch.setattr(ds, "_latest_allowed_bar_date", lambda: "2026-10-16")
    calls = []
    rows = [{"source": "tushare", "row_count": 3, "code_count": 2, "latest_trade_date": "2026-10-16", "latest_fetched_at": None}]
    monkeypatch.setattr(ds, "_compute_raw_bar_source_rows", lambda db, d: calls.append(d) or rows)
    assert ds._raw_bar_source_rows(None) == rows  # cold: computes + persists
    assert ds._raw_bar_source_rows(None) == rows  # warm: from disk
    assert calls == ["2026-10-16"]
    assert json.loads(path.read_text())["latest_allowed_bar_date"] == "2026-10-16"
    monkeypatch.setattr(ds, "_latest_allowed_bar_date", lambda: "2026-10-19")
    ds._raw_bar_source_rows(None)  # new bar date → synchronous recompute
    assert calls == ["2026-10-16", "2026-10-19"]
