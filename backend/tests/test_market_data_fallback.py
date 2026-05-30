from datetime import datetime
from types import MethodType, SimpleNamespace

import pandas as pd
import pytest

from app.engine.aggregator import aggregate
from app.engine.strategy_runner import _signal
from app.services import market_data
from app.services.market_data import (
    MarketDataError,
    _fetch_first_kline,
    _fetch_first_quotes,
    _fetch_kline,
    _fetch_quotes,
    _kline_sources,
    _quote_sources,
)


class DummySettings:
    market_request_timeout_seconds = 5
    market_scan_size = 60
    market_kline_days = 90
    market_candidate_count = 60
    quote_sources = "tushare"
    kline_sources = "tushare"
    data_provider = "tushare"
    market_candidate_lookahead_multiplier = 4


def test_fetch_first_quotes_raises_when_tushare_fails(monkeypatch):
    def fake_fetch_quotes(source, settings):
        raise RuntimeError(f"{source} down")

    monkeypatch.setattr(market_data, "_fetch_quotes", fake_fetch_quotes)

    with pytest.raises(MarketDataError) as exc:
        _fetch_first_quotes(["tushare"], DummySettings())

    message = str(exc.value)
    assert "真实行情快照获取失败" in message
    assert "tushare down" in message


def test_fetch_first_kline_falls_back_when_tushare_returns_too_few(monkeypatch):
    quote = {"code": "000001", "exchange": "SZ"}

    def fake_fetch_kline(source, quote_arg, settings):
        return []

    monkeypatch.setattr(market_data, "_fetch_kline", fake_fetch_kline)

    bars, source, errors = _fetch_first_kline(quote, ["tushare"], DummySettings())

    assert bars == []
    assert source == ""
    assert "日K不足30条" in " ".join(errors)


def test_fetch_first_kline_raises_upstream_errors_to_caller(monkeypatch):
    quote = {"code": "000001", "exchange": "SZ"}

    def fake_fetch_kline(source, quote_arg, settings):
        raise RuntimeError(f"{source} bad")

    monkeypatch.setattr(market_data, "_fetch_kline", fake_fetch_kline)

    bars, source, errors = _fetch_first_kline(quote, ["tushare"], DummySettings())

    assert bars == []
    assert source == ""
    assert "tushare: tushare bad" in errors


def test_fetch_tushare_quotes_uses_batch_quote_api(monkeypatch):
    class FakePro:
        def stock_basic(self, **kwargs):
            return pd.DataFrame(
                [
                    {"symbol": "000001", "name": "平安银行", "industry": "银行", "market": "主板"},
                    {"symbol": "600000", "name": "浦发银行", "industry": "银行", "market": "主板"},
                ]
            )

        def trade_cal(self, **kwargs):
            return pd.DataFrame([{"cal_date": "20260526"}, {"cal_date": "20260527"}])

        def bak_daily(self, **kwargs):
            assert kwargs["trade_date"] == "20260527"
            return pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": "20260527",
                        "close": 10.5,
                        "pct_change": 1.2,
                        "vol": 123456,
                        "amount": 456789,
                        "turn_over": 4.6,
                        "pe": 8.2,
                        "float_mv": 12000000000,
                        "total_mv": 18000000000,
                    },
                    {
                        "ts_code": "600000.SH",
                        "trade_date": "20260527",
                        "close": 12.3,
                        "pct_change": -0.8,
                        "vol": 654321,
                        "amount": 987654,
                        "turn_over": 3.1,
                        "pe": 6.5,
                        "float_mv": 22000000000,
                        "total_mv": 32000000000,
                    },
                ]
            )

        def daily(self, **kwargs):
            raise AssertionError("batch bak_daily 已成功时不应回退到 daily")

        def daily_basic(self, **kwargs):
            raise RuntimeError("daily_basic rate limited")

    monkeypatch.setattr("app.services.tushare_client.get_pro", lambda: FakePro())
    monkeypatch.setattr(market_data, "_load_local_stock_basics", lambda: [])
    monkeypatch.setattr(market_data, "_throttle_tushare", lambda _settings: None)

    quotes = market_data._fetch_tushare_quotes(DummySettings())

    assert len(quotes) == 2
    first = next(item for item in quotes if item["code"] == "000001")
    assert first["trade_date"] == "2026-05-27"
    assert first["turnover_rate"] == 4.6
    assert first["pe_ttm"] == 8.2
    assert first["free_mv_yi"] == 120.0
    assert first["total_mv_yi"] == 180.0
    assert first["amount_yi"] > 0


def test_fetch_tushare_quotes_prefers_local_stock_basics(monkeypatch):
    class FakePro:
        def stock_basic(self, **kwargs):
            raise AssertionError("已有本地 stocks 时不应再请求 stock_basic")

        def trade_cal(self, **kwargs):
            return pd.DataFrame([{"cal_date": "20260526"}, {"cal_date": "20260527"}])

        def bak_daily(self, **kwargs):
            assert kwargs["trade_date"] == "20260527"
            return pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": "20260527",
                        "close": 10.5,
                        "pct_change": 1.2,
                        "vol": 123456,
                        "amount": 456789,
                        "turn_over": 4.6,
                        "pe": 8.2,
                        "float_mv": 12000000000,
                        "total_mv": 18000000000,
                    }
                ]
            )

        def daily(self, **kwargs):
            raise AssertionError("batch bak_daily 已成功时不应回退到 daily")

        def daily_basic(self, **kwargs):
            return pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": "20260527",
                        "turnover_rate": 4.8,
                        "pe_ttm": 8.0,
                        "total_mv": 18100000000,
                        "circ_mv": 12100000000,
                    }
                ]
            )

    monkeypatch.setattr("app.services.tushare_client.get_pro", lambda: FakePro())
    monkeypatch.setattr(
        market_data,
        "_load_local_stock_basics",
        lambda: [
            {
                "symbol": "000001",
                "name": "平安银行",
                "industry": "银行",
                "market": "主板",
                "list_date": "1991-04-03",
                "is_st": False,
            }
        ],
    )
    monkeypatch.setattr(market_data, "_throttle_tushare", lambda _settings: None)

    quotes = market_data._fetch_tushare_quotes(DummySettings())

    assert len(quotes) == 1
    first = quotes[0]
    assert first["code"] == "000001"
    assert first["name"] == "平安银行"
    assert first["industry"] == "银行"
    assert first["trade_date"] == "2026-05-27"
    assert first["turnover_rate"] == 4.6
    assert first["exchange"] == "SZ"


def test_fetch_tushare_quotes_falls_back_from_empty_latest_trade_date(monkeypatch):
    class FakePro:
        def stock_basic(self, **kwargs):
            raise AssertionError("已有本地 stocks 时不应再请求 stock_basic")

        def trade_cal(self, **kwargs):
            return pd.DataFrame([{"cal_date": "20260526"}, {"cal_date": "20260527"}, {"cal_date": "20260528"}])

        def bak_daily(self, **kwargs):
            if kwargs["trade_date"] == "20260528":
                return pd.DataFrame([])
            if kwargs["trade_date"] == "20260527":
                return pd.DataFrame(
                    [
                        {
                            "ts_code": "000001.SZ",
                            "trade_date": "20260527",
                            "close": 10.5,
                            "pct_change": 1.2,
                            "vol": 123456,
                            "amount": 456789,
                            "turn_over": 4.6,
                            "pe": 8.2,
                            "float_mv": 12000000000,
                            "total_mv": 18000000000,
                        }
                    ]
                )
            raise AssertionError(f"unexpected trade_date: {kwargs['trade_date']}")

        def daily(self, **kwargs):
            raise AssertionError("bak_daily 已成功回退时不应改走 daily")

        def daily_basic(self, **kwargs):
            assert kwargs["trade_date"] == "20260527"
            return pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": "20260527",
                        "turnover_rate": 4.8,
                        "pe_ttm": 8.0,
                        "total_mv": 18100000000,
                        "circ_mv": 12100000000,
                    }
                ]
            )

    monkeypatch.setattr("app.services.tushare_client.get_pro", lambda: FakePro())
    monkeypatch.setattr(
        market_data,
        "_load_local_stock_basics",
        lambda: [
            {
                "symbol": "000001",
                "name": "平安银行",
                "industry": "银行",
                "market": "主板",
                "list_date": "1991-04-03",
                "is_st": False,
            }
        ],
    )
    monkeypatch.setattr(market_data, "_throttle_tushare", lambda _settings: None)

    quotes = market_data._fetch_tushare_quotes(DummySettings())

    assert len(quotes) == 1
    assert quotes[0]["trade_date"] == "2026-05-27"


def test_fetch_tushare_quotes_skips_incomplete_latest_snapshot(monkeypatch):
    symbols = [f"000{i:03d}" for i in range(1, 26)]
    api_calls: list[tuple[str, str]] = []

    def quote_rows(trade_date: str, *, amount: float, turnover: float) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "ts_code": f"{symbol}.SZ",
                    "trade_date": trade_date,
                    "close": 10.5,
                    "pct_change": 1.2,
                    "vol": 123456,
                    "amount": amount,
                    "turn_over": turnover,
                    "pe": 8.2,
                    "float_mv": 12000000000,
                    "total_mv": 18000000000,
                }
                for symbol in symbols
            ]
        )

    class FakePro:
        def stock_basic(self, **kwargs):
            raise AssertionError("已有本地 stocks 时不应再请求 stock_basic")

        def trade_cal(self, **kwargs):
            return pd.DataFrame([{"cal_date": "20260527"}, {"cal_date": "20260528"}])

        def bak_daily(self, **kwargs):
            trade_date = kwargs["trade_date"]
            api_calls.append(("bak_daily", trade_date))
            if trade_date == "20260528":
                return quote_rows(trade_date, amount=0, turnover=0)
            if trade_date == "20260527":
                return quote_rows(trade_date, amount=456789, turnover=4.6)
            raise AssertionError(f"unexpected trade_date: {trade_date}")

        def daily(self, **kwargs):
            trade_date = kwargs["trade_date"]
            api_calls.append(("daily", trade_date))
            if trade_date == "20260528":
                return quote_rows(trade_date, amount=0, turnover=0)
            raise AssertionError(f"unexpected daily fallback: {trade_date}")

        def daily_basic(self, **kwargs):
            assert kwargs["trade_date"] == "20260527"
            return pd.DataFrame(
                [
                    {
                        "ts_code": f"{symbol}.SZ",
                        "trade_date": "20260527",
                        "turnover_rate": 4.8,
                        "pe_ttm": 8.0,
                        "total_mv": 18100000000,
                        "circ_mv": 12100000000,
                    }
                    for symbol in symbols
                ]
            )

    monkeypatch.setattr("app.services.tushare_client.get_pro", lambda: FakePro())
    monkeypatch.setattr(
        market_data,
        "_load_local_stock_basics",
        lambda: [
            {
                "symbol": symbol,
                "name": f"测试{symbol}",
                "industry": "软件服务",
                "market": "主板",
                "list_date": "1991-04-03",
                "is_st": False,
            }
            for symbol in symbols
        ],
    )
    monkeypatch.setattr(market_data, "_throttle_tushare", lambda _settings: None)

    quotes = market_data._fetch_tushare_quotes(DummySettings())

    assert {quote["trade_date"] for quote in quotes} == {"2026-05-27"}
    assert len(quotes) == len(symbols)
    assert ("bak_daily", "20260528") in api_calls
    assert ("daily", "20260528") in api_calls
    assert ("bak_daily", "20260527") in api_calls


def test_fetch_tushare_quotes_requires_daily_basic_for_daily_fallback(monkeypatch):
    symbols = [f"000{i:03d}" for i in range(1, 26)]
    api_calls: list[tuple[str, str]] = []

    def quote_rows(trade_date: str, *, amount: float, turnover: float | None = None) -> pd.DataFrame:
        rows = []
        for symbol in symbols:
            row = {
                "ts_code": f"{symbol}.SZ",
                "trade_date": trade_date,
                "close": 10.5,
                "pct_change": 1.2,
                "vol": 123456,
                "amount": amount,
                "pe": 8.2,
                "float_mv": 12000000000,
                "total_mv": 18000000000,
            }
            if turnover is not None:
                row["turn_over"] = turnover
            rows.append(row)
        return pd.DataFrame(rows)

    def daily_basic_rows(trade_date: str, *, turnover: float) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "ts_code": f"{symbol}.SZ",
                    "trade_date": trade_date,
                    "turnover_rate": turnover,
                    "pe_ttm": 8.0,
                    "total_mv": 18100000000,
                    "circ_mv": 12100000000,
                }
                for symbol in symbols
            ]
        )

    class FakePro:
        def stock_basic(self, **kwargs):
            raise AssertionError("已有本地 stocks 时不应再请求 stock_basic")

        def trade_cal(self, **kwargs):
            return pd.DataFrame([{"cal_date": "20260527"}, {"cal_date": "20260528"}])

        def bak_daily(self, **kwargs):
            trade_date = kwargs["trade_date"]
            api_calls.append(("bak_daily", trade_date))
            if trade_date == "20260528":
                return pd.DataFrame([])
            if trade_date == "20260527":
                return quote_rows(trade_date, amount=456789, turnover=4.6)
            raise AssertionError(f"unexpected trade_date: {trade_date}")

        def daily(self, **kwargs):
            trade_date = kwargs["trade_date"]
            api_calls.append(("daily", trade_date))
            if trade_date == "20260528":
                return quote_rows(trade_date, amount=456789)
            raise AssertionError(f"unexpected daily fallback: {trade_date}")

        def daily_basic(self, **kwargs):
            trade_date = kwargs["trade_date"]
            api_calls.append(("daily_basic", trade_date))
            if trade_date == "20260528":
                return pd.DataFrame([])
            if trade_date == "20260527":
                return daily_basic_rows(trade_date, turnover=4.8)
            raise AssertionError(f"unexpected daily_basic date: {trade_date}")

    monkeypatch.setattr("app.services.tushare_client.get_pro", lambda: FakePro())
    monkeypatch.setattr(
        market_data,
        "_load_local_stock_basics",
        lambda: [
            {
                "symbol": symbol,
                "name": f"测试{symbol}",
                "industry": "软件服务",
                "market": "主板",
                "list_date": "1991-04-03",
                "is_st": False,
            }
            for symbol in symbols
        ],
    )
    monkeypatch.setattr(market_data, "_throttle_tushare", lambda _settings: None)

    quotes = market_data._fetch_tushare_quotes(DummySettings())

    assert {quote["trade_date"] for quote in quotes} == {"2026-05-27"}
    assert len(quotes) == len(symbols)
    assert ("daily", "20260528") in api_calls
    assert ("daily_basic", "20260528") in api_calls
    assert ("bak_daily", "20260527") in api_calls


def test_recent_open_trade_dates_excludes_unclosed_current_session(monkeypatch):
    class FakeDateTime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 5, 28, 9, 30)

    class FakePro:
        def trade_cal(self, **kwargs):
            return pd.DataFrame(
                [
                    {"cal_date": "20260526"},
                    {"cal_date": "20260527"},
                    {"cal_date": "20260528"},
                ]
            )

    monkeypatch.setattr(market_data, "datetime", FakeDateTime)
    monkeypatch.setattr("app.services.tushare_client.get_pro", lambda: FakePro())
    monkeypatch.setattr(market_data, "_throttle_tushare", lambda _settings: None)

    assert market_data._recent_open_trade_dates(DummySettings()) == ["20260526", "20260527"]


def test_call_tushare_with_retry_retries_rate_limit(monkeypatch):
    calls = {"count": 0}
    sleeps: list[float] = []

    class FakePro:
        def daily(self, **kwargs):
            calls["count"] += 1
            if calls["count"] < 3:
                raise RuntimeError("Tushare daily: 您请求速度过快")
            return "ok"

    monkeypatch.setattr(market_data.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(market_data, "_throttle_tushare", lambda _settings: None)

    result = market_data._call_tushare_with_retry(FakePro(), DummySettings(), "daily", trade_date="20260526")

    assert result == "ok"
    assert calls["count"] == 3
    assert sleeps == [1.5, 3.0]


def test_small_market_sample_caps_final_score_honestly():
    stock = {
        "code": "000001",
        "name": "平安银行",
        "market_sample_count": 6,
        "data_completeness_score": 100,
        "evidence_quality_score": 100,
        "data_quality": {"missing_fields": []},
    }
    signal = _signal(
        SimpleNamespace(skill_id="test", name="测试策略", version="v1", contract_hash="abcdef12"),
        score=100,
        phase="强势",
        hard_veto=False,
        setup_quality=100,
    )

    card = aggregate(stock, [signal], market_score=100, agent_review_score=100)

    assert card["final_score"] == 68.0
    assert "样本或证据覆盖不足，评分与 Agent 加分已降权" in card["info_tags"]

def test_authenticity_treats_composite_scores_as_observed_when_inputs_are_real():
    from app.services.feature_builder import build_features

    stock = {
        "code": "000001",
        "name": "平安银行",
        "industry": "银行",
        "price": 10.0,
        "pct": 1.2,
        "amount_yi": 12.0,
        "turnover_rate": 3.0,
        "free_mv_yi": 500.0,
        "total_mv_yi": 1000.0,
        "chg20": 12.0,
        "chg60": 18.0,
        "dist20": 3.0,
        "dist60": 6.0,
        "vol5_20": 1.2,
        "upper_shadow_count20": 1,
        "max_dd20": -4.0,
        "data_quality": {"strict_real_data": True, "missing_fields": []},
    }
    snapshot = {"sample_count": 5000, "main_industries": ["银行"], "breadth": {"regime": "温和"}}

    enriched = build_features([stock], snapshot)[0]

    assert enriched["_feature_tags"]["capital_flow_score"] == "OBSERVED"
    assert enriched["_feature_tags"]["fundamental_score"] == "OBSERVED"
    assert enriched["_feature_tags"]["intelligence_score"] == "OBSERVED"
    assert enriched["data_authenticity"] >= 0.8


def test_default_presets_do_not_overwrite_user_skill_selection(production_mysql):
    from app.db.session import SessionLocal
    from app.models.tables import Preset, PresetSkill, SkillRegistry
    from app.services import workbench_service

    db = SessionLocal()
    original_commit = db.commit

    def fake_commit(self):
        self.flush()

    try:
        db.commit = MethodType(fake_commit, db)
        Preset.__table__.create(bind=db.bind, checkfirst=True)
        SkillRegistry.__table__.create(bind=db.bind, checkfirst=True)
        PresetSkill.__table__.create(bind=db.bind, checkfirst=True)
        preset_ids = [item["preset_id"] for item in workbench_service.DEFAULT_PRESETS]
        db.query(PresetSkill).filter(PresetSkill.preset_id.in_(preset_ids)).delete(synchronize_session=False)
        db.query(Preset).filter(Preset.preset_id.in_(preset_ids)).delete(synchronize_session=False)
        db.query(SkillRegistry).filter(
            SkillRegistry.skill_id.in_(
                ["chen-xiaoqun-skill", "chao-gu-yang-jia-skill", "daily-stock-analysis-skill"]
            )
        ).delete(synchronize_session=False)
        db.flush()

        db.add_all(
            [
                SkillRegistry(
                    skill_id="chen-xiaoqun-skill",
                    name="陈小群",
                    source_type="local",
                    installed_path="skills/chen-xiaoqun-skill",
                    current_version="1.0.0",
                    mode="formal",
                    enabled=True,
                    has_executable_contract=False,
                    manifest_json="{}",
                ),
                SkillRegistry(
                    skill_id="chao-gu-yang-jia-skill",
                    name="炒股养家",
                    source_type="local",
                    installed_path="skills/chao-gu-yang-jia-skill",
                    current_version="1.0.0",
                    mode="formal",
                    enabled=True,
                    has_executable_contract=False,
                    manifest_json="{}",
                ),
                SkillRegistry(
                    skill_id="daily-stock-analysis-skill",
                    name="DSA分析",
                    source_type="local",
                    installed_path="skills/daily-stock-analysis-skill",
                    current_version="1.0.0",
                    mode="formal",
                    enabled=True,
                    has_executable_contract=False,
                    manifest_json="{}",
                ),
            ]
        )
        db.flush()

        db.add(
            Preset(
                preset_id="preset_formal_opening",
                name="正式盘前组合",
                run_mode="formal",
                market_scope="all",
                risk_template="balanced",
                output_count=6,
                bucket_limits_json='{}',
                data_source="local_pool",
                hide_recent_repeats=True,
                status="active",
            )
        )
        db.add(
            PresetSkill(
                id="preset_formal_opening:chen-xiaoqun-skill",
                preset_id="preset_formal_opening",
                skill_id="chen-xiaoqun-skill",
                weight=1.0,
                sort_order=0,
            )
        )
        db.commit()

        class Skill:
            def __init__(self, skill_id, groups):
                self.skill_id = skill_id
                self.contract = {"meta": {"default_preset_groups": groups}}

        skills = [
            Skill(
                "chen-xiaoqun-skill",
                {
                    "formal_opening": {"weight": 1.05, "sort_order": 10},
                    "formal_trend_core": {"weight": 1.15, "sort_order": 10},
                },
            ),
            Skill(
                "chao-gu-yang-jia-skill",
                {
                    "formal_opening": {"weight": 1.0, "sort_order": 20},
                    "formal_trend_core": {"weight": 1.05, "sort_order": 20},
                },
            ),
            Skill(
                "daily-stock-analysis-skill",
                {
                    "formal_opening": {"weight": 1.15, "sort_order": 30},
                    "formal_trend_core": {"weight": 0.95, "sort_order": 30},
                    "experiment_single_factor": {"weight": 1.0, "sort_order": 10},
                },
            ),
            Skill(
                "metadata-declared-trend-skill",
                {"formal_trend_core": {"weight": 0.88, "sort_order": 25}},
            ),
        ]
        workbench_service._ensure_default_presets(db, skills)

        rows = db.query(PresetSkill).filter(PresetSkill.preset_id == "preset_formal_opening").order_by(PresetSkill.sort_order).all()
        assert [row.skill_id for row in rows] == ["chen-xiaoqun-skill"]
        assert [row.weight for row in rows] == [1.0]

        created = db.get(Preset, "preset_formal_trend_core")
        assert created is not None
        created_rows = db.query(PresetSkill).filter(PresetSkill.preset_id == "preset_formal_trend_core").order_by(PresetSkill.sort_order).all()
        assert [row.skill_id for row in created_rows] == [
            "chen-xiaoqun-skill",
            "chao-gu-yang-jia-skill",
            "metadata-declared-trend-skill",
            "daily-stock-analysis-skill",
        ]
        assert [row.weight for row in created_rows] == [1.15, 1.05, 0.88, 0.95]
    finally:
        db.rollback()
        db.commit = original_commit
        db.close()
