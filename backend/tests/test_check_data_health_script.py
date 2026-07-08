import json
import sys

from scripts import check_data_health


class FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_check_data_health_json_uses_service_output(monkeypatch, capsys):
    expected = {
        "score": 90,
        "reference_trade_date": "2026-06-05",
        "reference_trade_date_source": "tushare",
        "issues": [],
        "tables": {"minute_kbar": {"max_date": "2026-06-05", "total_rows": 10}},
        "lag_days": {"minute_kbar": 0},
    }

    monkeypatch.setattr(sys, "argv", ["check_data_health.py", "--json"])
    monkeypatch.setattr(check_data_health, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(check_data_health, "diagnose_all", lambda db, **kw: expected)

    result = check_data_health.main()

    assert result == expected
    output = capsys.readouterr().out
    assert json.loads(output) == expected
