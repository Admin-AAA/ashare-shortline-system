from app.agents.hallucination_check import verify_review_output


def _stock(**overrides):
    stock = {
        "code": "605117",
        "name": "德业股份",
        "industry": "电气设备",
        "price": 158.96,
        "ma5": 156.97,
        "ma10": 152.31,
        "ma20": 148.08,
        "ma60": 128.54,
        "bias_ma5": 1.27,
        "macd_dif": 0.6902,
        "macd_dea": 0.566,
        "macd_bar": 0.2484,
        "macd_status": "零轴上金叉",
        "rsi_6": 66.95,
        "rsi_12": 62.95,
        "rsi_24": 59.95,
        "rsi_status": "强势买入",
        "dsa_trend_score": 26,
        "dsa_bias_score": 20,
        "dsa_volume_score": 10,
        "dsa_support_score": 5,
        "dsa_macd_score": 15,
        "dsa_rsi_score": 8,
    }
    stock.update(overrides)
    return stock


def _snapshot():
    return {
        "trade_date": "2026-05-13",
        "sample_count": 3105,
        "up_count": 1738,
        "down_count": 1320,
        "limit_up_count": 79,
        "limit_down_count": 3,
        "main_industries": ["元器件", "半导体", "电气设备"],
        "breadth": {"regime": "结构性强势"},
    }


def test_dsa_score_numbers_are_traceable():
    review = {
        "evidence": [
            "趋势评分26/30：均线多头排列，MA5>MA10>MA20",
            "乖离评分20/20：股价偏离MA5仅1.27%，处于最优买点区间",
            "MACD评分15/15：零轴上金叉，趋势确认较强",
            "RSI评分8/10：RSI12=62.95，强势未过热",
        ],
        "risks": [],
        "market_regime_judgment": "结构性强势",
    }

    result = verify_review_output(review, _stock(), _snapshot(), rule_signal=None)

    assert result["severity"] == "low"
    assert not any("数值无法追溯" in flag for flag in result["flags"])


def test_missing_indicator_claim_is_flagged_when_field_exists():
    review = {
        "evidence": ["MACD评分15/15：零轴上金叉，趋势确认较强"],
        "risks": ["RSI具体数值未提供，仅推断在50-70区间"],
        "market_regime_judgment": "结构性强势",
    }

    result = verify_review_output(review, _stock(), _snapshot(), rule_signal=None)

    assert result["severity"] == "medium"
    assert any("数据缺失声明与输入矛盾: RSI" in flag for flag in result["flags"])


def test_normal_risk_language_is_not_missing_data_claim():
    review = {
        "evidence": [
            "乖离评分20/20：股价27.79偏离MA5仅-0.88%，处于最优买点区间，无追高风险",
            "无否决条件触发：均线非空头、乖离<8%、RSI<80、无明确负面公告",
        ],
        "risks": ["RSI(12)62.95已接近70，若继续冲高可能进入超买区"],
        "market_regime_judgment": "结构性强势",
    }

    result = verify_review_output(review, _stock(price=27.79, bias_ma5=-0.88, rsi_12=62.95), _snapshot(), rule_signal=None)

    assert result["severity"] == "low"
    assert not any("数据缺失声明与输入矛盾" in flag for flag in result["flags"])
    assert not any("'70'" in flag or "'80'" in flag for flag in result["flags"])
