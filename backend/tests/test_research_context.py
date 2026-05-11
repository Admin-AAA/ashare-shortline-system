from app.services.research_context import build_research_context


def test_research_context_projects_skill_input_without_raw_transport_fields():
    stock = {
        "code": "000001",
        "name": "平安银行",
        "exchange": "SZ",
        "industry": "银行",
        "price": 10.5,
        "pct": 1.2,
        "amount_yi": 30.0,
        "turnover_rate": 2.0,
        "chg20": 8.0,
        "quote_source": "sina",
        "kline_source": "tencent",
        "latest_trade_date": "2026-05-08",
        "quote_time": "2026-05-09T01:00:00",
        "data_source": "real_market",
        "data_quality": {"strict_real_data": True, "kline_days": 90, "missing_fields": []},
        "_bars": [{"code": "000001", "trade_date": "2026-05-08", "close": 10.5}],
        "dsa_profile": {"ma5": 10.0},
        "_is_main": True,
    }
    snapshot = {
        "snapshot_id": "snapshot_test",
        "trade_date": "2026-05-08",
        "main_industries": ["银行"],
        "breadth": {"data_source": "real_market", "regime": "强势", "risk": "中"},
    }

    context = build_research_context(stock, snapshot)
    skill_input = context.to_skill_input()

    assert skill_input["research_context_version"] == "research_context.v1"
    assert "_bars" not in skill_input
    assert "dsa_profile" not in skill_input
    assert skill_input["_is_main"] is True
    assert skill_input["source_trace"]["quote_source"] == "sina"
    assert skill_input["source_trace"]["kline_source"] == "tencent"
    assert "research_context" in context.to_feature_snapshot()


def test_research_context_marks_missing_fact_domains_and_imputed_fields():
    stock = {
        "code": "000001",
        "name": "平安银行",
        "exchange": "SZ",
        "industry": "未分类",
        "price": 10.5,
        "pct": 1.2,
        "amount_yi": 30.0,
        "turnover_rate": 2.0,
        "main_net_inflow_yi": 0.0,
        "quote_source": "sina",
        "kline_source": "tencent",
        "latest_trade_date": "2026-05-08",
        "data_source": "real_market",
        "latest_news": "未接入实时新闻源",
        "announcement_negative_count": 0,
        "dragon_tiger_recent_count": 0,
        "data_quality": {"strict_real_data": True, "kline_days": 90, "missing_fields": ["main_net_inflow"]},
    }
    snapshot = {
        "snapshot_id": "snapshot_test",
        "trade_date": "2026-05-08",
        "main_industries": [],
        "breadth": {"data_source": "real_market", "regime": "震荡", "risk": "中高"},
    }

    context = build_research_context(stock, snapshot)
    quality = context.to_skill_input()["data_quality"]

    assert "main_net_inflow" in quality["missing_fields"]
    assert "industry" in quality["missing_fields"]
    assert "news" in quality["missing_fields"]
    assert "announcements" in quality["missing_fields"]
    assert "theme_tags" in quality["missing_fields"]
    assert quality["field_quality"]["main_net_inflow_yi"]["status"] == "imputed"
    assert quality["field_quality"]["industry"]["status"] == "missing"
    assert quality["data_completeness_score"] < 75
