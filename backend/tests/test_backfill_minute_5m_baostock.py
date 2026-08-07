from __future__ import annotations

import sys
from datetime import date, timedelta
from types import ModuleType

import pytest

from scripts import backfill_minute_5m_baostock as script

_active_codes = script._active_codes
_done_codes = script._done_codes


class _RowsResult:
    def __init__(self, rows: list[tuple[object, ...]]):
        self._rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return self._rows


class _CapturingDB:
    def __init__(self, rows: list[tuple[object, ...]]):
        self._rows = rows
        self.statement = None

    def execute(self, statement):
        self.statement = statement
        return _RowsResult(self._rows)


class _SequentialDB:
    def __init__(self, row_batches: list[list[tuple[object, ...]]]):
        self._row_batches = list(row_batches)
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        return _RowsResult(self._row_batches.pop(0))


def test_active_codes_uses_closed_window_and_keeps_valid_codes_without_stock_metadata():
    db = _CapturingDB(
        [
            ("600000",),
            ("000001",),
            ("300750",),
            ("430047",),
            ("830799",),
            ("920118",),
            ("921118",),
            ("967711",),
            ("977483",),
        ]
    )

    codes = _active_codes(db, "2026-01-30", "2026-07-28")

    assert codes == ["000001", "300750", "430047", "600000", "830799", "920118"]
    compiled = db.statement.compile()
    assert set(compiled.params.values()) == {"2026-01-30", "2026-07-28"}
    sql = str(compiled).lower()
    assert "daily_kbars.trade_date >=" in sql
    assert "daily_kbars.trade_date <=" in sql
    assert "stocks" not in sql
    assert "list_date" not in sql


def test_completed_codes_for_date_requires_strict_grid_and_valid_values():
    db = _CapturingDB([("600000",)])

    completed = script._completed_codes_for_date(db, "5min", "2026-07-27")

    assert completed == {"600000"}
    compiled = db.statement.compile()
    sql = str(compiled).lower()
    assert "minute_kbars.trade_date =" in sql
    assert "count(minute_kbars.id) =" in sql
    assert "count(distinct(right(minute_kbars.trade_time" in sql
    assert "sum(case when" in sql
    assert script._BARS_PER_DAY_5MIN in compiled.params.values()


def test_resume_plan_scans_each_trade_date_and_returns_minimum_gap_range():
    db = _SequentialDB(
        [
            [
                ("000001", "2026-07-27"),
                ("300750", "2026-07-27"),
                ("600000", "2026-07-27"),
                ("000001", "2026-07-28"),
                ("300750", "2026-07-28"),
                ("600000", "2026-07-28"),
            ],
            [("000001",), ("600000",)],
            [("600000",)],
        ]
    )

    done, pending = script._resume_plan(
        db,
        "5min",
        "2026-07-27",
        "2026-07-28",
    )

    assert done == {"600000"}
    assert pending == {
        "000001": ("2026-07-28", "2026-07-28"),
        "300750": ("2026-07-27", "2026-07-28"),
    }
    assert len(db.statements) == 3
    for statement in db.statements[1:]:
        compiled = statement.compile()
        assert "minute_kbars.trade_date =" in str(compiled).lower()


def test_market_selector_separates_beijing_from_shanghai_shenzhen():
    assert script._matches_market("600000", "shsz") is True
    assert script._matches_market("300750", "shsz") is True
    assert script._matches_market("920118", "shsz") is False
    assert script._matches_market("920118", "bse") is True
    assert script._matches_market("600000", "bse") is False
    assert script._matches_market("920118", "all") is True
    with pytest.raises(ValueError, match="unsupported market"):
        script._matches_market("600000", "unknown")


def test_beijing_stock_maps_to_eastmoney_secid():
    assert script._eastmoney_secid("920118") == "0.920118"


def test_eastmoney_date_chunks_cover_closed_window_without_overlap():
    chunks = script._date_chunks("2026-01-30", "2026-03-15")

    assert chunks[0][0] == "2026-01-30"
    assert chunks[-1][1] == "2026-03-15"
    for (chunk_start, chunk_end), (next_start, _next_end) in zip(chunks, chunks[1:]):
        assert date.fromisoformat(next_start) == date.fromisoformat(chunk_end) + timedelta(days=1)
    assert all(
        (date.fromisoformat(chunk_end) - date.fromisoformat(chunk_start)).days < 20
        for chunk_start, chunk_end in chunks
    )


def test_eastmoney_rows_convert_volume_to_shares_and_keep_amount_unchanged():
    rows = script._parse_eastmoney_klines(
        "920118",
        "5min",
        ["2026-07-28 09:35,10.10,10.20,10.30,10.00,1234,987654.32,0,0,0,0"],
    )

    assert rows == [
        {
            "code": "920118",
            "freq": "5min",
            "trade_date": "2026-07-28",
            "trade_time": "2026-07-28 09:35:00",
            "open": 10.1,
            "high": 10.3,
            "low": 10.0,
            "close": 10.2,
            "volume": 123400.0,
            "amount": 987654.32,
            "source": "eastmoney",
        }
    ]


def test_eastmoney_fetch_uses_serial_date_chunks_and_expected_request_contract(monkeypatch):
    calls: list[dict] = []

    class _Response:
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self):
            return None

        def json(self):
            begin = calls[-1]["params"]["beg"]
            trade_day = date(
                int(begin[0:4]),
                int(begin[4:6]),
                int(begin[6:8]),
            ).isoformat()
            return {
                "data": {
                    "klines": [
                        f"{trade_day} 09:35,10,10.1,10.2,9.9,1,25,0,0,0,0"
                    ]
                }
            }

    class _Client:
        def get(self, url, *, params, timeout):
            calls.append({"url": url, "params": params, "timeout": timeout})
            return _Response()

    monkeypatch.setattr(script, "_wait_for_eastmoney_rate_limit", lambda: None)
    rows = script._fetch_eastmoney_minute_kbars(
        "920118",
        "5min",
        "2026-01-30",
        "2026-03-15",
        client=_Client(),
    )

    assert len(calls) == len(script._date_chunks("2026-01-30", "2026-03-15"))
    assert len(rows) == len(calls)
    assert all(call["params"]["secid"] == "0.920118" for call in calls)
    assert all(call["params"]["klt"] == "5" for call in calls)
    assert all(call["params"]["fqt"] == "0" for call in calls)
    assert all(call["params"]["lmt"] == "1152" for call in calls)
    assert all(
        call["timeout"] == script._EASTMONEY_TIMEOUT_SECONDS for call in calls
    )
    assert [
        (call["params"]["beg"], call["params"]["end"])
        for call in calls
    ] == [
        (start.replace("-", ""), end.replace("-", ""))
        for start, end in script._date_chunks("2026-01-30", "2026-03-15")
    ]


def test_eastmoney_fetch_rejects_response_at_provider_row_limit(monkeypatch):
    class _Response:
        status_code = 200
        headers: dict[str, str] = {}

        def json(self):
            return {
                "data": {
                    "klines": [
                        "2026-07-28 09:35,10,10.1,10.2,9.9,1,25,0,0,0,0"
                    ]
                    * script._EASTMONEY_ROW_LIMIT
                }
            }

    class _Client:
        def get(self, url, *, params, timeout):
            return _Response()

    monkeypatch.setattr(script, "_wait_for_eastmoney_rate_limit", lambda: None)
    with pytest.raises(script.EastmoneyFetchError) as exc_info:
        script._fetch_eastmoney_minute_kbars(
            "920118",
            "5min",
            "2026-07-28",
            "2026-07-28",
            client=_Client(),
        )

    assert exc_info.value.category == "truncated_response"
    assert exc_info.value.retryable is False


def test_eastmoney_fetch_classifies_non_retryable_http_failure(monkeypatch):
    attempts = 0

    class _Response:
        status_code = 404
        headers: dict[str, str] = {}

        def json(self):
            return {}

    class _Client:
        def get(self, url, *, params, timeout):
            nonlocal attempts
            attempts += 1
            return _Response()

    monkeypatch.setattr(script, "_wait_for_eastmoney_rate_limit", lambda: None)
    with pytest.raises(script.EastmoneyFetchError) as exc_info:
        script._fetch_eastmoney_minute_kbars(
            "920118",
            "5min",
            "2026-07-28",
            "2026-07-28",
            client=_Client(),
        )

    assert exc_info.value.category == "http_client"
    assert exc_info.value.retryable is False
    assert attempts == 1


def test_eastmoney_fetch_retries_transient_failure_through_rate_limit(monkeypatch):
    statuses = [503, 200]
    waits: list[None] = []
    sleeps: list[float] = []

    class _Response:
        headers: dict[str, str] = {}

        def __init__(self, status_code: int):
            self.status_code = status_code

        def json(self):
            return {
                "data": {
                    "klines": [
                        "2026-07-28 09:35,10,10.1,10.2,9.9,1,25,0,0,0,0"
                    ]
                }
            }

    class _Client:
        def get(self, url, *, params, timeout):
            return _Response(statuses.pop(0))

    monkeypatch.setattr(
        script,
        "_wait_for_eastmoney_rate_limit",
        lambda: waits.append(None),
    )
    monkeypatch.setattr(script.time, "sleep", sleeps.append)

    rows = script._fetch_eastmoney_minute_kbars(
        "920118",
        "5min",
        "2026-07-28",
        "2026-07-28",
        client=_Client(),
    )

    assert len(rows) == 1
    assert waits == [None, None]
    assert sleeps == [0.5]
    assert statuses == []


def test_fetch_rows_routes_only_beijing_codes_to_eastmoney(monkeypatch):
    eastmoney_calls: list[tuple[str, str, str, str]] = []
    baostock_calls: list[tuple[str, str, str, str]] = []
    tushare_stub = ModuleType("app.services.tushare_client")

    def fake_baostock(code, freq, start_date, end_date):
        baostock_calls.append((code, freq, start_date, end_date))
        return [{"code": code}]

    tushare_stub._fetch_baostock_minute_kbars = fake_baostock
    monkeypatch.setitem(sys.modules, "app.services.tushare_client", tushare_stub)
    monkeypatch.setattr(
        script,
        "_fetch_eastmoney_minute_kbars",
        lambda code, freq, start_date, end_date: eastmoney_calls.append(
            (code, freq, start_date, end_date)
        )
        or [{"code": code, "freq": freq}],
    )

    bj_rows = script._fetch_rows("920118", "5min", "2026-07-27", "2026-07-28")
    sh_rows = script._fetch_rows("600000", "5min", "2026-07-27", "2026-07-28")

    assert bj_rows == [{"code": "920118", "freq": "5min"}]
    assert eastmoney_calls == [("920118", "5min", "2026-07-27", "2026-07-28")]
    assert baostock_calls == [("600000", "5min", "2026-07-27", "2026-07-28")]
    assert sh_rows == [{"code": "600000", "freq": "5min"}]


def _valid_day_rows(code: str = "600000", trade_date: str = "2026-07-28") -> list[dict]:
    return [
        {
            "code": code,
            "freq": "5min",
            "trade_date": trade_date,
            "trade_time": f"{trade_date} {bar_time}",
            "open": 10.0,
            "high": 10.2,
            "low": 9.9,
            "close": 10.1,
            "volume": 1000.0,
            "amount": 10000.0,
        }
        for bar_time in sorted(script._EXPECTED_5MIN_TIMES)
    ]


def test_validated_rows_requires_exact_standard_grid_and_positive_prices():
    rows = _valid_day_rows()

    validated = script._validated_rows(
        rows,
        code="600000",
        freq="5min",
        expected_dates=("2026-07-28",),
    )

    assert len(validated) == 48
    assert validated[0]["trade_time"].endswith("09:35:00")
    assert validated[-1]["trade_time"].endswith("15:00:00")

    with pytest.raises(ValueError, match="数据不完整"):
        script._validated_rows(
            rows[:-1],
            code="600000",
            freq="5min",
            expected_dates=("2026-07-28",),
        )

    zero_rows = _valid_day_rows()
    zero_rows[0] = {**zero_rows[0], "open": 0.0}
    with pytest.raises(ValueError, match="价格非正"):
        script._validated_rows(
            zero_rows,
            code="600000",
            freq="5min",
            expected_dates=("2026-07-28",),
        )


def test_validated_rows_rejects_duplicate_and_nonstandard_times():
    duplicate_rows = _valid_day_rows()
    duplicate_rows[-1] = dict(duplicate_rows[0])
    with pytest.raises(ValueError, match="重复 trade_time"):
        script._validated_rows(
            duplicate_rows,
            code="600000",
            freq="5min",
            expected_dates=("2026-07-28",),
        )

    wrong_time_rows = _valid_day_rows()
    wrong_time_rows[-1] = {
        **wrong_time_rows[-1],
        "trade_time": "2026-07-28 15:01:00",
    }
    with pytest.raises(ValueError, match="非标准 5min 时间"):
        script._validated_rows(
            wrong_time_rows,
            code="600000",
            freq="5min",
            expected_dates=("2026-07-28",),
        )


def test_main_returns_nonzero_and_rolls_back_when_any_stock_fails(monkeypatch):
    class _DB:
        def __init__(self):
            self.rollback_count = 0
            self.closed = False

        def rollback(self):
            self.rollback_count += 1

        def close(self):
            self.closed = True

    db = _DB()
    monkeypatch.setattr(script, "SessionLocal", lambda: db)
    monkeypatch.setattr(script, "_active_codes", lambda *_args: ["600000"])
    monkeypatch.setattr(
        script,
        "_expected_trade_dates",
        lambda *_args: ("2026-07-28",),
    )
    monkeypatch.setattr(script, "_fetch_rows", lambda *_args: [])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backfill_minute_5m_baostock.py",
            "--start-date",
            "2026-07-28",
            "--end-date",
            "2026-07-28",
        ],
    )

    assert script.main() == 1
    assert db.rollback_count == 1
    assert db.closed is True
