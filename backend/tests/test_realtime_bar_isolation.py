"""盘中实时拼接的临时日K不得污染 daily_kbars，以及能把这种污染拦下来的三道防线（L-159）。

2026-09-21 推荐默认数据源改成「本地历史 + 当天实时」后，每次运行都把内存里拼出的实时日K
写回 daily_kbars：成交量按股（日线按手，差 100 倍）、换手率 0、收盘后运行时昨收取的是当日
自身收盘价，涨跌幅恒 0。09-21 起约 4950 只股票的日线成交量全是按股写入，09-28 起涨跌幅与
换手率也被写成 0，健康闸门照常放行盘后推荐。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd

from app.services.data_gap_remediation_service import _backfill_daily_kbar_date
from app.services.data_health_service import recommendation_readiness_from_diagnosis
from app.services.market_pool import core as pool_core
from app.services.market_source import InMemoryMarketSourceAdapter
from app.services.runs.coordinator import StageData
from app.services.runs.stages.market_data import MarketDataStage


def _bar(code: str, trade_date: str, close: float, *, source: str | None = None) -> dict:
    bar = {
        "code": code,
        "trade_date": trade_date,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1000.0,
        "amount": close * 1000.0 * 100,
        "pct_chg": 1.0,
        "turnover_rate": 2.0,
    }
    if source:
        bar["source"] = source
    return bar


def test_persist_skips_transient_realtime_bars():
    from app.services import workbench_service as wb

    written: list[tuple[str, str]] = []

    class _Db:
        def execute(self, stmt):
            if stmt.table.name != "daily_kbars":
                return
            params = stmt.compile().params
            index = 0
            while f"code_m{index}" in params:
                written.append((params[f"code_m{index}"], params[f"trade_date_m{index}"]))
                index += 1

    stock = {
        "code": "600001",
        "name": "x",
        "_bars": [
            _bar("600001", "2026-10-08", 10.0),
            _bar("600001", "2026-10-09", 10.1, source="realtime_quote"),
        ],
    }
    wb._persist_stocks_and_bars(_Db(), [stock], persist_bars=True)
    assert written == [("600001", "2026-10-08")]


class _RecordingPersistence:
    def __init__(self):
        self.calls: list[str] = []

    def persist_snapshot(self, snapshot):
        self.calls.append("snapshot")

    def persist_stocks_and_bars(self, stocks, *, persist_bars):
        self.calls.append("stocks_and_bars")

    def commit_shared_market_facts(self):
        self.calls.append("commit")

    def persist_run_market_context(self, snapshot, *, universe_count):
        self.calls.append("run_context")


class _Universe:
    def load_universe(self, *, data_source, as_of_date):
        return ({"code": "600000"},), {"trade_date": "2026-10-09", "breadth": {}}, {}


def test_local_pool_realtime_run_does_not_write_bars_back():
    persistence = _RecordingPersistence()
    stage = MarketDataStage(market_universe=_Universe(), market_persistence=persistence)
    stage.execute(SimpleNamespace(run_id="run_x"), StageData(params={"data_source": "local_pool_realtime"}))
    assert "stocks_and_bars" not in persistence.calls


def test_realtime_bar_volume_is_converted_to_lots():
    row = {"DATE": "20261009", "PRICE": 11.59, "OPEN": 11.78, "HIGH": 11.89, "LOW": 11.58,
           "VOLUME": 107810561.0, "AMOUNT": 1259717108.28}
    bar = pool_core._build_realtime_bar(row, 11.78, "000001")
    assert bar["volume"] == 1078105.61
    assert round(bar["pct_chg"], 4) == -1.6129
    assert bar["source"] == "realtime_quote"


def test_realtime_bar_after_close_uses_previous_day_close(monkeypatch):
    """收盘后运行：实时K线与库里最后一条同日，昨收必须取前一日收盘价而不是当日自身。"""
    quote = {
        "code": "000001", "name": "平安银行", "exchange": "SZ", "industry": "银行",
        "trade_date": "2026-10-09", "price": 11.59, "pct": -1.61, "amount_yi": 12.6,
        "turnover_rate": 0.55, "free_mv_yi": 2249.0, "total_mv_yi": 2249.0, "is_st": False,
        "preclose": 11.78, "volume": 107810561.0,
    }
    history = [_bar("000001", f"2026-08-{day:02d}", 11.0 + day / 100) for day in range(1, 31)]
    history.append(_bar("000001", "2026-10-08", 11.78))
    history.append(_bar("000001", "2026-10-09", 11.59))
    frame = pd.DataFrame([{
        "TS_CODE": "000001.SZ", "DATE": "20261009", "PRICE": 11.59, "OPEN": 11.78, "HIGH": 11.89,
        "LOW": 11.58, "VOLUME": 107810561.0, "AMOUNT": 1259717108.28,
    }])
    monkeypatch.setattr(pool_core, "_load_quotes_from_pool", lambda db, settings, skip_metrics=False: ([quote], {"errors": []}))
    monkeypatch.setattr(pool_core, "_fetch_tushare_realtime_frame", lambda codes, settings: frame)
    monkeypatch.setattr(pool_core, "_load_cached_bars_batch", lambda db, codes, limit: {"000001": list(history)})

    captured: dict[str, list[dict]] = {}

    def _capture(quote_row, bars, **kwargs):
        captured["bars"] = bars
        return {"code": quote_row["code"], "_bars": bars}

    monkeypatch.setattr(pool_core, "_build_stock_from_bars", _capture)
    monkeypatch.setattr(pool_core, "_apply_relative_features", lambda stocks: None)
    monkeypatch.setattr(pool_core, "_build_snapshot", lambda **kwargs: {"trade_date": "2026-10-09", "breadth": {}})
    monkeypatch.setattr(pool_core, "_build_quote_snapshot", lambda *args, **kwargs: {})
    monkeypatch.setattr(pool_core, "_merge_quality", lambda *args, **kwargs: {})

    pool_core.get_market_universe_from_pool_with_realtime(MagicMock())

    bars = captured["bars"]
    assert [b["trade_date"] for b in bars].count("2026-10-09") == 1
    assert bars[-2]["trade_date"] == "2026-10-08"
    assert bars[-1]["source"] == "realtime_quote"
    assert round(bars[-1]["pct_chg"], 4) == -1.6129
    assert bars[-1]["volume"] == 1078105.61


def _rows_with_nonzero_ratio(total: int, nonzero: int) -> dict[str, list[dict]]:
    rows: dict[str, list[dict]] = {}
    for index in range(total):
        code = f"{600000 + index:06d}"
        rows[code] = [{
            "code": code, "trade_date": "2026-10-09", "open": 10, "high": 10, "low": 10, "close": 10,
            "vol": 1000, "volume": 1000, "amount": 1000000,
            "pct_chg": 1.5 if index < nonzero else 0.0, "turnover_rate": 0.0,
        }]
    return rows


def test_daily_batch_refuses_partially_published_trade_date():
    """少数股票已有涨跌幅、多数仍为 0 的整批日线不得写库（原判据只拦全零）。"""
    db = MagicMock()
    bars = _rows_with_nonzero_ratio(200, 20)
    source = InMemoryMarketSourceAdapter(bars_by_code=bars)
    result = _backfill_daily_kbar_date(db, "2026-10-09", list(bars), market_source=source)
    assert result["row_count"] == 0
    assert result["status"] == "skipped"
    db.execute.assert_not_called()


def test_daily_batch_writes_fully_published_trade_date():
    db = MagicMock()
    bars = _rows_with_nonzero_ratio(200, 190)
    source = InMemoryMarketSourceAdapter(bars_by_code=bars)
    result = _backfill_daily_kbar_date(db, "2026-10-09", list(bars), market_source=source)
    assert result["row_count"] == 200
    db.execute.assert_called()


def _diagnosis_with_sentinel(distorted: list[dict], *, sentinel_date: str = "2026-10-09") -> dict:
    from app.services.data_health_service import _RECOMMENDATION_REQUIRED_TABLES

    return {
        "reference_trade_date": "2026-10-09",
        "tables": {key: {"label": key, "max_date": "2026-10-09"} for key in _RECOMMENDATION_REQUIRED_TABLES},
        "recent_trade_day_coverage": {"tables": {}},
        "value_distribution": {"reference_trade_date": sentinel_date, "distorted": distorted},
        "issues": [],
    }


def test_readiness_blocks_when_critical_quote_column_distorted():
    readiness = recommendation_readiness_from_diagnosis(_diagnosis_with_sentinel([
        {"column": "pct_chg", "label": "涨跌幅", "critical": True, "reason": "非零占比过低（10.5% < 期望 50%）"},
    ]))
    assert readiness["can_run_local_recommendation"] is False
    detail = readiness["blocker_details"][0]
    assert detail["code"] == "value_distribution_distorted"
    assert detail["column"] == "pct_chg"
    assert "涨跌幅" in readiness["blockers"][0]


def test_readiness_ignores_non_critical_and_other_day_distortion():
    readiness = recommendation_readiness_from_diagnosis(_diagnosis_with_sentinel([
        {"column": "ma5", "label": "MA5", "critical": False, "reason": "非零占比过低"},
    ]))
    assert readiness["can_run_local_recommendation"] is True
    stale = recommendation_readiness_from_diagnosis(_diagnosis_with_sentinel(
        [{"column": "pct_chg", "label": "涨跌幅", "critical": True, "reason": "x"}],
        sentinel_date="2026-10-08",
    ))
    assert stale["can_run_local_recommendation"] is True


def _insert_day(db, trade_date: str, *, volume_in_shares: int, total: int = 150) -> None:
    from sqlalchemy.dialects.mysql import insert as mysql_insert
    from datetime import datetime

    from app.models.tables import DailyKbar

    rows = []
    for index in range(total):
        close = 10.0 + index / 100
        lots = 1000.0 + index
        shares = index < volume_in_shares
        rows.append({
            "code": f"{900000 + index:06d}", "trade_date": trade_date,
            "open": close, "high": close, "low": close, "close": close,
            "volume": lots * 100 if shares else lots, "amount": lots * 100 * close,
            "pct_chg": 1.0 + index / 1000, "turnover_rate": 1.0 + index / 1000,
            "ma5": close, "ma20": close, "bias_5": 1.0 + index / 1000, "vol_ratio_5": 1.0 + index / 1000,
            "max_dd20": -1.0 - index / 1000, "upper_shadow_count20": index % 4, "breakout20": index % 2,
            "source": "tushare", "updated_at": datetime(2099, 1, 2),
        })
    db.execute(mysql_insert(DailyKbar).values(rows))
    db.flush()


def test_sentinel_flags_volume_written_in_shares(db):
    from app.services.data_health_service import _value_distribution_sentinel

    _insert_day(db, "2099-01-02", volume_in_shares=120)
    sentinel = _value_distribution_sentinel(db, "2099-01-02")
    volume = [item for item in sentinel["distorted"] if item["column"] == "volume"]
    assert volume and volume[0]["critical"] is True
    assert volume[0]["volume_in_shares"] == 120
    assert "按股" in volume[0]["reason"]


def test_sentinel_accepts_volume_in_lots(db):
    from app.services.data_health_service import _value_distribution_sentinel

    _insert_day(db, "2099-01-03", volume_in_shares=3)
    sentinel = _value_distribution_sentinel(db, "2099-01-03")
    assert not [item for item in sentinel["distorted"] if item["column"] == "volume"]


def test_daily_batch_upsert_keeps_indicators_when_batch_has_none():
    """stk_factor 无权限时批量补数整批指标为空，不得把库里补算好的 MA/乖离清成 NULL。"""
    from sqlalchemy.dialects import mysql

    from app.services.data_gap_remediation_service import _upsert_daily_kbar_payload

    statements: list[str] = []

    class _Db:
        def execute(self, stmt):
            statements.append(str(stmt.compile(dialect=mysql.dialect())))

        def commit(self):
            pass

    _upsert_daily_kbar_payload(_Db(), [{"code": "600001", "trade_date": "2026-10-09", "close": 10.0, "ma5": None}])
    sql = statements[0]
    update_clause = sql.split("ON DUPLICATE KEY UPDATE", 1)[1]
    assert "ma5 = coalesce(nullif(VALUES(ma5)" in update_clause
    assert "pct_chg = VALUES(pct_chg)" in update_clause
    assert "breakout20" not in update_clause
