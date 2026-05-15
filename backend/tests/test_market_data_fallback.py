from types import SimpleNamespace

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
    quote_sources = "thsdk,eastmoney"
    kline_sources = "tencent,thsdk,baostock"
    data_provider = "multi"
    market_candidate_lookahead_multiplier = 4
    thsdk_batch_size = 600
    thsdk_username = ""
    thsdk_password = ""
    thsdk_mac = ""


def test_fetch_first_quotes_falls_back_to_second_source(monkeypatch):
    calls = []

    def fake_fetch_quotes(source, settings):
        calls.append(source)
        if source == "eastmoney":
            raise RuntimeError("eastmoney down")
        if source == "sina":
            return [{"code": "000001", "name": "平安银行"}]
        return []

    monkeypatch.setattr(market_data, "_fetch_quotes", fake_fetch_quotes)

    source, quotes, errors = _fetch_first_quotes(["eastmoney", "sina", "tencent"], DummySettings())

    assert source == "sina"
    assert quotes == [{"code": "000001", "name": "平安银行"}]
    assert calls == ["eastmoney", "sina"]
    assert errors == ["eastmoney: eastmoney down"]


def test_fetch_first_quotes_raises_when_all_sources_fail(monkeypatch):
    def fake_fetch_quotes(source, settings):
        raise RuntimeError(f"{source} down")

    monkeypatch.setattr(market_data, "_fetch_quotes", fake_fetch_quotes)

    with pytest.raises(MarketDataError) as exc:
        _fetch_first_quotes(["eastmoney", "sina", "tencent"], DummySettings())

    message = str(exc.value)
    assert "真实行情快照获取失败" in message
    assert "eastmoney: eastmoney down" in message
    assert "sina: sina down" in message
    assert "tencent: tencent down" in message


def test_fetch_first_kline_falls_back_to_second_source(monkeypatch):
    quote = {"code": "000001", "exchange": "SZ"}
    calls = []

    def fake_fetch_kline(source, quote_arg, settings):
        calls.append(source)
        if source == "eastmoney":
            raise RuntimeError("empty reply")
        if source == "tencent":
            return [
                {
                    "code": "000001",
                    "trade_date": f"2026-01-{day:02d}",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.9,
                    "close": 10.1,
                    "volume": 1000,
                    "amount": 10000,
                    "pct_chg": 1.0,
                    "turnover_rate": 1.0,
                }
                for day in range(1, 31)
            ]
        return []

    monkeypatch.setattr(market_data, "_fetch_kline", fake_fetch_kline)

    bars, source, errors = _fetch_first_kline(quote, ["eastmoney", "tencent", "baostock"], DummySettings())

    assert source == "tencent"
    assert len(bars) == 30
    assert calls == ["eastmoney", "tencent"]
    assert errors == ["eastmoney: empty reply"]


def test_fetch_first_kline_raises_upstream_errors_to_caller(monkeypatch):
    quote = {"code": "000001", "exchange": "SZ"}

    def fake_fetch_kline(source, quote_arg, settings):
        raise RuntimeError(f"{source} bad")

    monkeypatch.setattr(market_data, "_fetch_kline", fake_fetch_kline)

    bars, source, errors = _fetch_first_kline(quote, ["eastmoney", "tencent", "baostock"], DummySettings())

    assert bars == []
    assert source == ""
    assert errors == [
        "eastmoney: eastmoney bad",
        "tencent: tencent bad",
        "baostock: baostock bad",
    ]


def test_fetch_first_kline_disables_global_baostock_login_failures(monkeypatch):
    quote = {"code": "000001", "exchange": "SZ"}
    disabled_sources = set()
    calls = []

    def fake_fetch_kline(source, quote_arg, settings):
        calls.append(source)
        if source == "baostock":
            raise RuntimeError("baostock login failed: 网络接收错误")
        return []

    monkeypatch.setattr(market_data, "_fetch_kline", fake_fetch_kline)

    bars, source, errors = _fetch_first_kline(quote, ["baostock", "tencent"], DummySettings(), disabled_sources)
    bars2, source2, errors2 = _fetch_first_kline(quote, ["baostock", "tencent"], DummySettings(), disabled_sources)

    assert bars == []
    assert source == ""
    assert "baostock" in disabled_sources
    assert errors[0] == "baostock: baostock login failed: 网络接收错误"
    assert bars2 == []
    assert source2 == ""
    assert calls == ["baostock", "tencent", "tencent"]
    assert errors2[0] == "baostock: 本次扫描已熔断，跳过"


def test_thsdk_and_thssdk_aliases_route_to_same_adapters(monkeypatch):
    quote = {"code": "000001", "exchange": "SZ"}

    monkeypatch.setattr(market_data, "_fetch_thsdk_quotes", lambda settings: [{"code": "000001"}])
    monkeypatch.setattr(market_data, "_fetch_thsdk_kline", lambda quote_arg, settings: [{"code": quote_arg["code"]}])

    assert _fetch_quotes("thsdk", DummySettings()) == [{"code": "000001"}]
    assert _fetch_quotes("thssdk", DummySettings()) == [{"code": "000001"}]
    assert _fetch_kline("thsdk", quote, DummySettings()) == [{"code": "000001"}]
    assert _fetch_kline("thssdk", quote, DummySettings()) == [{"code": "000001"}]


def test_data_provider_accepts_thsdk_aliases_and_baostock_kline_only():
    settings = DummySettings()
    settings.data_provider = "thssdk"
    assert _quote_sources(settings) == ["thssdk"]
    assert _kline_sources(settings) == ["thssdk"]

    settings.data_provider = "baostock"
    assert _quote_sources(settings) == ["thsdk", "eastmoney"]
    assert _kline_sources(settings) == ["baostock"]


def test_fetch_thsdk_quotes_merges_split_rows(monkeypatch):
    class FakeTHS:
        def __init__(self, ops):
            self.ops = ops

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return None

        def stock_cn_lists(self):
            return SimpleNamespace(
                success=True,
                data=[
                    {"THSCODE": "USZA000001", "名称": "平安银行"},
                    {"THSCODE": "USHA600000", "名称": "浦发银行"},
                ],
            )

        def market_data_cn(self, batch, query_key="汇总"):
            assert query_key == "汇总"
            if batch == ["USZA000001"]:
                return SimpleNamespace(
                    success=True,
                    data=[
                        {"代码": "USZA000001", "价格": "10.50", "涨幅": "1.2", "总金额": "300000000"},
                        {"代码": "USZA000001", "换手率": "2.5", "总市值": "200000000000", "流通市值": "180000000000"},
                    ],
                )
            return SimpleNamespace(
                success=True,
                data=[
                    {
                        "代码": "USHA600000",
                        "价格": "9.50",
                        "涨幅": "-0.4",
                        "总金额": "210000000",
                        "换手率": "1.1",
                        "市盈率TTM": "6.8",
                        "主力净流入": "12000000",
                    }
                ],
            )

    monkeypatch.setattr(market_data, "_thsdk_client", lambda settings: FakeTHS({}))
    settings = DummySettings()
    settings.market_scan_size = 2
    settings.thsdk_batch_size = 1

    quotes = market_data._fetch_thsdk_quotes(settings)

    assert [quote["code"] for quote in quotes] == ["000001", "600000"]
    assert quotes[0]["name"] == "平安银行"
    assert quotes[0]["price"] == 10.5
    assert quotes[0]["amount_yi"] == 3.0
    assert quotes[0]["turnover_rate"] == 2.5
    assert quotes[0]["source"] == "thsdk"
    assert quotes[1]["main_net_inflow_yi"] == 0.12


def test_fetch_thsdk_kline_maps_rows_and_computes_pct(monkeypatch):
    class FakeTHS:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return None

        def klines(self, ths_code, count, adjust):
            assert ths_code == "USZA300033"
            assert count == 2
            assert adjust == "forward"
            return SimpleNamespace(
                success=True,
                data=[
                    {"时间": "20260507", "开盘价": "10", "最高价": "11", "最低价": "9.8", "收盘价": "10.5", "成交量": "1000", "总金额": "1050000"},
                    {"时间": "20260508", "开盘价": "10.6", "最高价": "12", "最低价": "10.5", "收盘价": "11.55", "成交量": "1200", "总金额": "1386000"},
                ],
            )

    monkeypatch.setattr(market_data, "_thsdk_client", lambda settings: FakeTHS())
    settings = DummySettings()
    settings.market_kline_days = 2

    bars = market_data._fetch_thsdk_kline({"code": "300033", "exchange": "SZ"}, settings)

    assert bars[0]["trade_date"] == "2026-05-07"
    assert bars[0]["close"] == 10.5
    assert bars[0]["pct_chg"] == 0.0
    assert bars[1]["trade_date"] == "2026-05-08"
    assert bars[1]["pct_chg"] == pytest.approx(10.0)


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
    assert "市场样本仅6只，评分已按覆盖率降权" in card["risk_flags"]

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


def test_default_presets_do_not_overwrite_user_skill_selection(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.tables import Base, Preset, PresetSkill
    from app.services import workbench_service

    db_path = tmp_path / "workbench.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
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
            def __init__(self, skill_id):
                self.skill_id = skill_id

        skills = [Skill("chen-xiaoqun-skill"), Skill("chao-gu-yang-jia-skill"), Skill("daily-stock-analysis-skill")]
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
            "daily-stock-analysis-skill",
        ]
    finally:
        db.close()
