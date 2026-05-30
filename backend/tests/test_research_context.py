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
        "is_main_theme": True,
        "is_shortline_leader_candidate": True,
        "shortline_leader_score": 82.0,
        "market_sentiment_phase": "活跃",
        "capital_resonance_score": 78.0,
        "capital_resonance_signal": "主力共振",
        "capital_resonance_source": "capital_flow",
        "dragon_tiger_buy_dispersion": "集中",
        "dragon_tiger_inst_buy_ratio": 0.66,
        "limit_up_overheat_score": 24.0,
        "limit_up_overheat_level": "低",
        "sector_ladder_completeness_score": 76.0,
        "sector_ladder_level": "完整",
        "twenty_cm_risk_gate": "pass",
        "shortline_factor_explanations": {
            "capital_resonance": {
                "fields": ["capital_resonance_score", "capital_resonance_signal"],
                "logic": "资金流共振",
                "data_sources": ["Tushare/本地资金流池"],
                "degradation": "缺少资金流时回退到价量",
                "source": "capital_flow",
            }
        },
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
    assert "_is_main" not in skill_input
    assert "_is_leader_candidate" not in skill_input
    assert "_sentiment_phase" not in skill_input
    assert "_consensus_signal" not in skill_input
    assert skill_input["is_main_theme"] is True
    assert skill_input["is_shortline_leader_candidate"] is True
    assert skill_input["market_sentiment_phase"] == "活跃"
    assert skill_input["capital_resonance_signal"] == "主力共振"
    assert skill_input["dragon_tiger_buy_dispersion"] == "集中"
    assert skill_input["dragon_tiger_inst_buy_ratio"] == 0.66
    assert skill_input["shortline_factor_explanations"]["capital_resonance"]["source"] == "capital_flow"
    assert context.to_dict()["themes"]["is_main_theme"] is True
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
    assert quality["field_quality"]["main_net_inflow_yi"]["status"] == "fallback"
    assert quality["field_quality"]["industry"]["status"] == "missing"
    assert quality["data_completeness_score"] < 75


def test_research_context_respects_checked_empty_auxiliary_quality_flags():
    stock = {
        "code": "000001",
        "name": "平安银行",
        "exchange": "SZ",
        "industry": "银行",
        "price": 10.5,
        "pct": 1.2,
        "amount_yi": 30.0,
        "turnover_rate": 2.0,
        "main_net_inflow_yi": 0.0,
        "quote_source": "tushare",
        "kline_source": "tushare",
        "latest_trade_date": "2026-05-12",
        "data_source": "real_market",
        "announcement_negative_count": 0,
        "dragon_tiger_is_on_list": False,
        "dragon_tiger_recent_count": 0,
        "theme_tags": [],
        "_feature_tags": {"main_net_inflow_yi": "OBSERVED"},
        "data_quality": {
            "strict_real_data": True,
            "kline_days": 90,
            "missing_fields": [
                "announcements",
                "capital_flow",
                "dragon_tiger",
                "main_net_inflow",
                "news",
                "theme_tags",
            ],
            "announcements_checked_empty": True,
            "capital_flow_checked": True,
            "dragon_tiger_checked_empty": True,
            "news_checked_empty": True,
            "theme_tags_fallback_from_industry": True,
        },
    }
    snapshot = {
        "snapshot_id": "snapshot_test",
        "trade_date": "2026-05-12",
        "main_industries": ["银行"],
        "breadth": {"data_source": "real_market", "regime": "震荡", "risk": "中"},
    }

    context = build_research_context(stock, snapshot)
    quality = context.to_skill_input()["data_quality"]

    assert "announcements" not in quality["missing_fields"]
    assert "capital_flow" not in quality["missing_fields"]
    assert "dragon_tiger" not in quality["missing_fields"]
    assert "main_net_inflow" not in quality["missing_fields"]
    assert "news" not in quality["missing_fields"]
    assert "theme_tags" not in quality["missing_fields"]
    assert quality["data_completeness_score"] == 100.0
