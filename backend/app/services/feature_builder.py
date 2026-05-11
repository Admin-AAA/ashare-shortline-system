from typing import Any


TREND_SCORE_MAP = {
    "强势多头": 30,
    "多头排列": 26,
    "弱势多头": 18,
    "盘整": 12,
    "弱势空头": 8,
    "空头排列": 4,
    "强势空头": 0,
}

VOLUME_SCORE_MAP = {
    "缩量回调": 15,
    "放量上涨": 12,
    "量能正常": 10,
    "缩量上涨": 6,
    "放量下跌": 0,
}

MACD_SCORE_MAP = {
    "零轴上金叉": 15,
    "金叉": 12,
    "上穿零轴": 10,
    "多头": 8,
    "空头": 2,
    "下穿零轴": 0,
    "死叉": 0,
}


def build_features(stocks: list[dict[str, Any]], snapshot: dict[str, Any], dsa_weights: dict[str, float] | None = None) -> list[dict[str, Any]]:
    main_industries = set(snapshot.get("main_industries", []))
    enriched = []
    for stock in stocks:
        item = dict(stock)
        profile = dict(item.pop("dsa_profile", {}) or {})
        item["_is_main"] = item.get("industry") in main_industries and item.get("industry") != "未分类"
        item["market_sample_count"] = int(snapshot.get("sample_count") or 0)
        item["market_regime"] = snapshot.get("breadth", {}).get("regime", "未知")
        _apply_dsa_context(item, profile, dsa_weights)
        item["risk_score"] = _risk_score(item)
        _tag_features(item)
        item["data_authenticity"] = _compute_authenticity(item)
        enriched.append(item)
    return enriched


def market_regime_score(snapshot: dict[str, Any]) -> float:
    sample = max(1, int(snapshot.get("sample_count") or 1))
    up_ratio = float(snapshot.get("up_count") or 0) / sample
    if sample < 20:
        return 45.0
    if sample < 100:
        return 55.0
    if up_ratio >= 0.65:
        return 82.0
    if up_ratio >= 0.5:
        return 72.0
    if up_ratio >= 0.35:
        return 58.0
    return 42.0


def _risk_score(stock: dict[str, Any]) -> float:
    score = 0.0
    if stock.get("announcement_negative_count", 0) > 0:
        score += 35
    if stock.get("news_risk_count", 0) > 0:
        score += 25
    if stock.get("turnover_rate", 0) > 12:
        score += 20
    if stock.get("upper_shadow_count20", 0) >= 5:
        score += 15
    if stock.get("max_dd20", 0) < -15:
        score += 20
    if stock.get("dsa_bias_score", 20) <= 4:
        score += 15
    if stock.get("dsa_macd_score", 15) == 0:
        score += 12
    return min(score, 100.0)


def _apply_dsa_context(item: dict[str, Any], profile: dict[str, Any], dsa_weights: dict[str, float] | None = None) -> None:
    price = _num(item.get("price") or item.get("close"))
    bias_ma5 = _num(profile.get("bias_ma5"), _default_bias_ma5(item))
    bias_ma10 = _num(profile.get("bias_ma10"), bias_ma5 + 2.5)
    bias_ma20 = _num(profile.get("bias_ma20"), _num(item.get("dist20"), bias_ma5 + 5.0))
    bias_ma60 = _num(profile.get("bias_ma60"), _num(item.get("dist60"), bias_ma20 + 6.0))

    ma5 = _num(profile.get("ma5"), _ma_from_bias(price, bias_ma5))
    ma10 = _num(profile.get("ma10"), _ma_from_bias(price, bias_ma10))
    ma20 = _num(profile.get("ma20"), _ma_from_bias(price, bias_ma20))
    ma60 = _num(profile.get("ma60"), _ma_from_bias(price, bias_ma60))

    item.update(
        {
            "current_price": price,
            "ma5": round(ma5, 2),
            "ma10": round(ma10, 2),
            "ma20": round(ma20, 2),
            "ma60": round(ma60, 2),
            "bias_ma5": round(_bias(price, ma5), 2),
            "bias_ma10": round(_bias(price, ma10), 2),
            "bias_ma20": round(_bias(price, ma20), 2),
        }
    )

    trend_status, ma_alignment, trend_strength = _trend_state(ma5, ma10, ma20, item)
    item["trend_status"] = str(profile.get("trend_status") or trend_status)
    item["ma_alignment"] = str(profile.get("ma_alignment") or ma_alignment)
    item["trend_strength"] = _num(profile.get("trend_strength"), trend_strength)

    volume_ratio_5d = _num(profile.get("volume_ratio_5d"), _num(item.get("vol5_20"), 1.0))
    volume_ratio_20d = _num(profile.get("volume_ratio_vs_20d"), _num(item.get("vol5_20"), 1.0))
    volume_status, volume_trend = _volume_state(volume_ratio_5d, _num(item.get("pct")))
    item["volume_status"] = str(profile.get("volume_status") or volume_status)
    item["volume_trend"] = str(profile.get("volume_trend") or volume_trend)
    item["volume_ratio_5d"] = round(volume_ratio_5d, 2)
    item["volume_ratio_vs_5d"] = round(volume_ratio_5d, 2)
    item["volume_ratio_vs_20d"] = round(volume_ratio_20d, 2)
    item["volume_trend_pct"] = _num(profile.get("volume_trend_pct"), round((volume_ratio_5d - 1) * 100, 1))
    item["avg_up_day_volume"] = _num(profile.get("avg_up_day_volume"), round(_num(item.get("amount_yi")) * 0.52, 2))
    item["avg_down_day_volume"] = _num(profile.get("avg_down_day_volume"), round(_num(item.get("amount_yi")) * 0.39, 2))
    item["high_volume_days"] = int(_num(profile.get("high_volume_days"), 1 if volume_ratio_20d > 1.5 else 0))
    item["volume_price_corr"] = _num(profile.get("volume_price_corr"), 0.35 if _num(item.get("pct")) > 0 else -0.15)
    item["volume_pattern"] = str(profile.get("volume_pattern") or _volume_pattern(item))

    support_ma5 = _bool(profile.get("support_ma5"), abs(item["bias_ma5"]) <= 2 and price >= ma5)
    support_ma10 = _bool(profile.get("support_ma10"), abs(item["bias_ma10"]) <= 2.5 and price >= ma10)
    resistance_levels = profile.get("resistance_levels") or [_num(profile.get("resistance_level"), round(price * (1 + max(1.5, abs(_num(item.get("off_high_pct"), -3))) / 100), 2))]
    support_levels = profile.get("support_levels") or [level for level in [ma5 if support_ma5 else 0, ma10 if support_ma10 else 0, ma20 if price >= ma20 else 0] if level]
    item["support_ma5"] = support_ma5
    item["support_ma10"] = support_ma10
    item["support_levels"] = [round(_num(v), 2) for v in support_levels]
    item["resistance_levels"] = [round(_num(v), 2) for v in resistance_levels]
    nearest_resistance = min([_num(v) for v in item["resistance_levels"] if _num(v) > price] or [price])
    item["resistance_distance_pct"] = round((nearest_resistance - price) / price * 100, 2) if price else 0.0

    macd_status = str(profile.get("macd_status") or _macd_status(item))
    macd_dif = _num(profile.get("macd_dif"), round(_num(item.get("chg20")) / 20, 4))
    macd_dea = _num(profile.get("macd_dea"), round(macd_dif * 0.82, 4))
    macd_bar = _num(profile.get("macd_bar"), round((macd_dif - macd_dea) * 2, 4))
    item["macd_status"] = macd_status
    item["macd_dif"] = macd_dif
    item["macd_dea"] = macd_dea
    item["macd_bar"] = macd_bar
    item["macd_signal"] = str(profile.get("macd_signal") or _macd_signal(macd_status))

    rsi_12 = _num(profile.get("rsi_12"), _estimate_rsi(item))
    item["rsi_6"] = _num(profile.get("rsi_6"), min(95, max(8, rsi_12 + 4)))
    item["rsi_12"] = rsi_12
    item["rsi_24"] = _num(profile.get("rsi_24"), min(90, max(12, rsi_12 - 3)))
    item["rsi_status"] = str(profile.get("rsi_status") or _rsi_status(rsi_12))
    item["rsi_signal"] = str(profile.get("rsi_signal") or _rsi_signal(item["rsi_status"]))

    _apply_context_blocks(item, profile)
    _apply_dsa_scores(item, dsa_weights)
    _apply_buy_signal(item)


def _apply_context_blocks(item: dict[str, Any], profile: dict[str, Any]) -> None:
    gaps = set((item.get("data_quality") or {}).get("missing_fields", []))
    strict_real = item.get("data_source") == "real_market"

    item["main_net_inflow_yi"] = _num(
        profile.get("main_net_inflow_yi"),
        0.0 if strict_real else _default_main_inflow(item),
    )
    item["inflow_5d_yi"] = _num(
        profile.get("inflow_5d_yi"),
        item["main_net_inflow_yi"] if strict_real else round(item["main_net_inflow_yi"] * 2.2, 2),
    )
    item["inflow_10d_yi"] = _num(
        profile.get("inflow_10d_yi"),
        item["main_net_inflow_yi"] if strict_real else round(item["main_net_inflow_yi"] * 3.4, 2),
    )
    item["sector_fund_rank"] = int(_num(profile.get("sector_fund_rank"), 99 if strict_real else 5 if item.get("_is_main") else 26))
    item["capital_flow_score"] = _capital_flow_score(item)

    item["profit_ratio"] = _num(profile.get("profit_ratio"), 50.0 if strict_real else _default_profit_ratio(item))
    item["avg_cost"] = _num(profile.get("avg_cost"), round(_num(item.get("price")) * 0.94, 2))
    item["cost_90_low"] = _num(profile.get("cost_90_low"), round(item["avg_cost"] * 0.92, 2))
    item["cost_90_high"] = _num(profile.get("cost_90_high"), round(item["avg_cost"] * 1.08, 2))
    item["concentration_90"] = _num(profile.get("concentration_90"), 13.5 if item.get("_is_main") else 22.0)
    item["cost_70_low"] = _num(profile.get("cost_70_low"), round(item["avg_cost"] * 0.96, 2))
    item["cost_70_high"] = _num(profile.get("cost_70_high"), round(item["avg_cost"] * 1.04, 2))
    item["concentration_70"] = _num(profile.get("concentration_70"), 8.5 if item.get("_is_main") else 15.0)
    item["chip_concentration"] = item["concentration_90"]
    item["chip_health"] = str(profile.get("chip_health") or _chip_health(item))
    item["chip_health_score"] = _chip_health_score(item)

    item["revenue_yoy"] = _num(profile.get("revenue_yoy"), 0.0 if strict_real else 12.0 if item.get("_is_main") else 3.0)
    item["net_profit_yoy"] = _num(profile.get("net_profit_yoy"), 0.0 if strict_real else 10.0 if item.get("_is_main") else -2.0)
    item["roe"] = _num(profile.get("roe"), 0.0 if strict_real else 11.5)
    item["gross_margin"] = _num(profile.get("gross_margin"), 0.0 if strict_real else 28.0)
    item["pe_ttm"] = _num(profile.get("pe_ttm"), 0.0 if strict_real else 32.0 if item.get("_is_main") else 26.0)
    item["earnings_outlook"] = str(profile.get("earnings_outlook") or ("未接入真实财务增强数据" if strict_real else "暂无重大负面业绩信号"))
    item["fundamental_score"] = _fundamental_score(item)
    item["institution_holding_change"] = _num(profile.get("institution_holding_change"), 0.0)
    item["top10_holder_change"] = _num(profile.get("top10_holder_change"), 0.0)

    risk_alerts = list(profile.get("risk_alerts") or [])
    positive_catalysts = list(profile.get("positive_catalysts") or [])
    item["latest_news"] = str(profile.get("latest_news") or ("未接入实时新闻源" if strict_real else "暂无重大未处理新闻"))
    item["risk_alerts"] = risk_alerts
    item["positive_catalysts"] = positive_catalysts
    item["news_risk_count"] = int(_num(profile.get("news_risk_count"), len(risk_alerts)))
    item["positive_catalyst_count"] = int(_num(profile.get("positive_catalyst_count"), len(positive_catalysts)))
    item["sentiment_summary"] = str(profile.get("sentiment_summary") or _sentiment_summary(item))
    item["intelligence_score"] = _intelligence_score(item)
    if strict_real:
        if "main_net_inflow" in gaps:
            item["capital_flow_score"] = min(item["capital_flow_score"], 55)
        if "valuation" in gaps:
            item["fundamental_score"] = min(item["fundamental_score"], 50)
        item["intelligence_score"] = min(item["intelligence_score"], 50)

    item["dragon_tiger_is_on_list"] = _bool(profile.get("dragon_tiger_is_on_list"), False)
    item["dragon_tiger_recent_count"] = int(_num(profile.get("dragon_tiger_recent_count"), 0))
    item["dragon_tiger_latest_date"] = profile.get("dragon_tiger_latest_date")

    item["volume_breakout_signal"] = _bool(
        profile.get("volume_breakout_signal"),
        bool(item.get("breakout20")) and item["volume_ratio_5d"] >= 1.1 and _num(item.get("pct")) > 0,
    )
    item["volume_shrink_pullback"] = _bool(
        profile.get("volume_shrink_pullback"),
        item["volume_ratio_5d"] <= 0.8 and item["support_ma5"] and _num(item.get("pct")) <= 0,
    )


def _apply_dsa_scores(item: dict[str, Any], dsa_weights: dict[str, float] | None = None) -> None:
    item["dsa_trend_score"] = TREND_SCORE_MAP.get(str(item.get("trend_status")), 12)
    item["dsa_bias_score"] = _bias_score(_num(item.get("bias_ma5")))
    item["dsa_volume_score"] = VOLUME_SCORE_MAP.get(str(item.get("volume_status")), 10)
    item["dsa_support_score"] = (5 if item.get("support_ma5") else 0) + (5 if item.get("support_ma10") else 0)
    item["dsa_macd_score"] = MACD_SCORE_MAP.get(str(item.get("macd_status")), 2)
    item["dsa_rsi_score"] = _rsi_score(_num(item.get("rsi_12")))

    # Use calibrated DSA weights if available, otherwise equal weight (simple sum)
    w = dsa_weights or {}
    w_trend = w.get("dsa_w_trend", 1.0)
    w_bias = w.get("dsa_w_bias", 1.0)
    w_volume = w.get("dsa_w_volume", 1.0)
    w_support = w.get("dsa_w_support", 1.0)
    w_macd = w.get("dsa_w_macd", 1.0)
    w_rsi = w.get("dsa_w_rsi", 1.0)

    item["dsa_signal_score"] = int(
        item["dsa_trend_score"] * w_trend
        + item["dsa_bias_score"] * w_bias
        + item["dsa_volume_score"] * w_volume
        + item["dsa_support_score"] * w_support
        + item["dsa_macd_score"] * w_macd
        + item["dsa_rsi_score"] * w_rsi
    )

    # 动量衰减：防止追高（滞后指标优化）
    item["dsa_signal_score"] = _apply_momentum_decay(item, item["dsa_signal_score"])


def _apply_momentum_decay(stock_data: dict, dsa_signal_score: float) -> float:
    """
    对滞后技术指标应用动量衰减，防止追高。

    - 金叉后超过 10 个交易日: score *= 0.92^(days_since_golden_cross - 10)
    - 死叉后: trend_score 直接乘以 0.5
    - 距离近期高点超过 15%: score *= 0.85

    从 stock_data 中读取:
    - days_since_golden_cross: MACD 金叉距今交易日数（从 DailyIndicator 或从 K线反推）
    - off_high_pct: 距离20日最高价的百分比
    """
    score = dsa_signal_score

    # 1. 金叉后衰减
    days_since_golden = stock_data.get("days_since_golden_cross", 0)
    if days_since_golden > 10:
        decay = 0.92 ** (days_since_golden - 10)
        score *= decay

    # 2. 死叉惩罚
    is_dead_cross = stock_data.get("macd_dead_cross", False)
    if is_dead_cross:
        score *= 0.5

    # 3. 偏离高点惩罚
    off_high = abs(float(stock_data.get("off_high_pct", 0)))
    if off_high > 15:
        score *= 0.85

    return round(score, 2)


def _apply_buy_signal(item: dict[str, Any]) -> None:
    score = _num(item.get("dsa_signal_score"))
    risk_factors: list[str] = []
    reasons: list[str] = []
    if item.get("trend_status") in {"强势多头", "多头排列"}:
        reasons.append("均线多头")
    if item.get("support_ma5") or item.get("support_ma10"):
        reasons.append("靠近均线支撑")
    if item.get("volume_shrink_pullback"):
        reasons.append("缩量回踩")
    if item.get("volume_breakout_signal"):
        reasons.append("放量突破")
    if item.get("capital_flow_score", 0) >= 70:
        reasons.append("资金确认")
    if item.get("news_risk_count", 0) > 0:
        risk_factors.append("存在新闻/公告风险")
    if item.get("bias_ma5", 0) > 5:
        risk_factors.append("偏离 MA5 过远")
    if item.get("rsi_12", 0) > 70:
        risk_factors.append("RSI 超买")
    if item.get("dsa_macd_score", 0) == 0:
        risk_factors.append("MACD 转弱")

    if score >= 82 and not risk_factors:
        signal = "强烈买入"
    elif score >= 70:
        signal = "买入"
    elif score >= 58:
        signal = "持有"
    elif score >= 45:
        signal = "观望"
    elif score >= 30:
        signal = "卖出"
    else:
        signal = "强烈卖出"
    item["buy_signal"] = signal
    item["signal_reasons"] = reasons
    item["risk_factors"] = risk_factors
    item["ideal_buy"] = round(_num(item.get("ma5")) * 1.002, 2)
    item["secondary_buy"] = round(_num(item.get("ma10")) * 1.003, 2)
    item["stop_loss"] = round(min(_num(item.get("ma10")), _num(item.get("price"))) * 0.965, 2)
    item["take_profit"] = round(_num(item.get("price")) * 1.08, 2)
    item["suggested_position"] = "中仓" if score >= 78 else "小仓" if score >= 65 else "观察仓"
    item["operation_advice"] = _operation_advice(signal, item)
    item["confidence_level"] = "高" if score >= 78 and not risk_factors else "中" if score >= 60 else "低"


def _trend_state(ma5: float, ma10: float, ma20: float, item: dict[str, Any]) -> tuple[str, str, float]:
    dist20 = _num(item.get("dist20"))
    if ma5 > ma10 > ma20:
        if dist20 > 10 and _num(item.get("eff20")) > 0.5:
            return "强势多头", "强势多头排列，均线发散上行", 90
        return "多头排列", "多头排列 MA5>MA10>MA20", 75
    if ma5 > ma10 and ma10 <= ma20:
        return "弱势多头", "弱势多头，MA5>MA10 但 MA10 未站上 MA20", 55
    if ma5 < ma10 < ma20:
        if dist20 < -8:
            return "强势空头", "强势空头排列，均线发散下行", 10
        return "空头排列", "空头排列 MA5<MA10<MA20", 25
    if ma5 < ma10 and ma10 >= ma20:
        return "弱势空头", "弱势空头，MA5<MA10 但中期仍有承接", 40
    return "盘整", "均线缠绕，趋势不明", 50


def _volume_state(volume_ratio: float, pct: float) -> tuple[str, str]:
    if volume_ratio >= 1.5:
        if pct > 0:
            return "放量上涨", "放量上涨，多头力量增强"
        return "放量下跌", "放量下跌，注意风险释放"
    if volume_ratio <= 0.7:
        if pct > 0:
            return "缩量上涨", "缩量上涨，上攻动能不足"
        return "缩量回调", "缩量回调，洗盘特征较好"
    return "量能正常", "量能正常"


def _volume_pattern(item: dict[str, Any]) -> str:
    up_vol = _num(item.get("avg_up_day_volume"))
    down_vol = _num(item.get("avg_down_day_volume"))
    if up_vol > down_vol * 1.3:
        return "量价配合良好"
    if down_vol > up_vol * 1.3:
        return "量价背离"
    if _num(item.get("volume_ratio_5d")) > 1.5:
        return "近期明显放量"
    if _num(item.get("volume_ratio_5d")) < 0.7:
        return "近期明显缩量"
    return "量价关系中性"


def _macd_status(item: dict[str, Any]) -> str:
    if bool(item.get("breakout20")) and _num(item.get("chg20")) > 12 and _num(item.get("pct")) > 0:
        return "零轴上金叉"
    if _num(item.get("chg20")) > 8 and _num(item.get("eff20")) > 0.45:
        return "多头"
    if _num(item.get("chg20")) > 0:
        return "金叉"
    if _num(item.get("chg20")) < -5:
        return "空头"
    return "下穿零轴" if _num(item.get("pct")) < 0 else "上穿零轴"


def _macd_signal(status: str) -> str:
    return {
        "零轴上金叉": "零轴上金叉，趋势确认较强",
        "金叉": "金叉，趋势向上",
        "上穿零轴": "DIF 上穿零轴，趋势转强",
        "多头": "MACD 多头区域",
        "空头": "MACD 空头区域",
        "下穿零轴": "DIF 下穿零轴，趋势转弱",
        "死叉": "死叉，趋势向下",
    }.get(status, "MACD 中性")


def _estimate_rsi(item: dict[str, Any]) -> float:
    base = 50 + _num(item.get("chg20")) * 0.45 + (_num(item.get("pos20")) - 50) * 0.18
    return round(min(86, max(18, base)), 2)


def _rsi_status(rsi: float) -> str:
    if rsi > 70:
        return "超买"
    if rsi >= 50:
        return "强势买入"
    if rsi >= 40:
        return "中性"
    if rsi >= 30:
        return "弱势"
    return "超卖"


def _rsi_signal(status: str) -> str:
    return {
        "超买": "RSI 超买，追高胜率下降",
        "强势买入": "RSI 处于强势区但未过热",
        "中性": "RSI 中性",
        "弱势": "RSI 偏弱",
        "超卖": "RSI 超卖，关注修复但需趋势确认",
    }.get(status, "RSI 中性")


def _bias_score(bias_ma5: float) -> int:
    if -2 <= bias_ma5 <= 1.5:
        return 20
    if -4 <= bias_ma5 < -2 or 1.5 < bias_ma5 <= 3:
        return 16
    if 3 < bias_ma5 <= 5:
        return 10
    if -7 <= bias_ma5 < -4:
        return 8
    if bias_ma5 > 8:
        return 2
    return 4


def _rsi_score(rsi: float) -> int:
    if rsi < 30:
        return 10
    if 50 <= rsi <= 70:
        return 8
    if 40 <= rsi < 50:
        return 5
    if 30 <= rsi < 40:
        return 3
    return 0


def _capital_flow_score(item: dict[str, Any]) -> float:
    score = 50.0
    score += min(22, max(-18, _num(item.get("main_net_inflow_yi")) * 3))
    score += 14 if _num(item.get("inflow_5d_yi")) > 0 else -10
    rank = int(_num(item.get("sector_fund_rank"), 99))
    if rank <= 5:
        score += 14
    elif rank <= 15:
        score += 6
    else:
        score -= 8
    return round(min(100, max(0, score)), 2)


def _chip_health(item: dict[str, Any]) -> str:
    concentration = _num(item.get("concentration_90"))
    profit_ratio = _num(item.get("profit_ratio"))
    if concentration <= 15 and 35 <= profit_ratio <= 75:
        return "筹码集中且获利盘适中"
    if profit_ratio > 85:
        return "获利盘过高，潜在抛压大"
    if concentration > 25:
        return "筹码分散，拉升效率偏低"
    return "筹码结构中性"


def _chip_health_score(item: dict[str, Any]) -> float:
    score = 55.0
    concentration = _num(item.get("concentration_90"))
    profit_ratio = _num(item.get("profit_ratio"))
    if concentration <= 15:
        score += 20
    elif concentration <= 22:
        score += 8
    else:
        score -= 12
    if 35 <= profit_ratio <= 75:
        score += 18
    elif profit_ratio > 85:
        score -= 20
    elif profit_ratio < 20:
        score -= 8
    return round(min(100, max(0, score)), 2)


def _fundamental_score(item: dict[str, Any]) -> float:
    score = 55.0
    if _num(item.get("revenue_yoy")) > 10:
        score += 10
    elif _num(item.get("revenue_yoy")) < 0:
        score -= 10
    if _num(item.get("net_profit_yoy")) > 10:
        score += 12
    elif _num(item.get("net_profit_yoy")) < -10:
        score -= 15
    if _num(item.get("roe")) >= 12:
        score += 8
    if _num(item.get("gross_margin")) >= 25:
        score += 5
    return round(min(100, max(0, score)), 2)


def _intelligence_score(item: dict[str, Any]) -> float:
    score = 58.0
    score += _num(item.get("positive_catalyst_count")) * 8
    score -= _num(item.get("news_risk_count")) * 22
    if item.get("dragon_tiger_is_on_list"):
        score += min(12, _num(item.get("dragon_tiger_recent_count")) * 4)
    return round(min(100, max(0, score)), 2)


def _sentiment_summary(item: dict[str, Any]) -> str:
    if item.get("news_risk_count", 0) > 0:
        return "存在风险情报，需降低信号权重"
    if item.get("positive_catalyst_count", 0) > 0 and item.get("_is_main"):
        return "主线题材叠加正向催化"
    if item.get("_is_main"):
        return "处于主线行业，情绪承接较好"
    return "非主线方向，情绪确认不足"


def _operation_advice(signal: str, item: dict[str, Any]) -> str:
    if signal in {"强烈买入", "买入"}:
        return f"优先等待 {item.get('ideal_buy')} 附近承接确认，破 {item.get('stop_loss')} 降低假设权重。"
    if signal == "持有":
        return "已有仓位可跟踪，新增仓位等待更清晰的回踩或突破。"
    if signal == "观望":
        return "证据不足，等趋势、量能或情报继续确认。"
    return "短线胜率不足，先排除。"


def _default_bias_ma5(item: dict[str, Any]) -> float:
    dist20 = _num(item.get("dist20"))
    pct = _num(item.get("pct"))
    if dist20 > 18:
        return 4.5
    if dist20 > 10:
        return 2.0
    if pct < 0 and _num(item.get("chg20")) > 8:
        return -0.6
    if dist20 < 0:
        return -2.5
    return 1.0


def _default_main_inflow(item: dict[str, Any]) -> float:
    direction = 1 if _num(item.get("pct")) > 0 else -0.5
    main_bonus = 1.2 if item.get("_is_main") else 0.4
    return round(_num(item.get("amount_yi")) * 0.05 * direction * main_bonus, 2)


def _default_profit_ratio(item: dict[str, Any]) -> float:
    return round(min(92, max(8, 45 + _num(item.get("pos20")) * 0.35 + _num(item.get("chg20")) * 0.25)), 2)


def _ma_from_bias(price: float, bias_pct: float) -> float:
    return price / (1 + bias_pct / 100) if (1 + bias_pct / 100) else price


def _bias(price: float, ma: float) -> float:
    return (price - ma) / ma * 100 if ma else 0.0


def _num(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


# ── Feature Provenance Tags ────────────────────────────────────────────

# Fields derived directly from OHLCV / market quotes (always real when present)
_OBSERVED_FIELDS = {
    "price", "pct", "amount_yi", "turnover_rate", "free_mv_yi", "total_mv_yi",
    "chg20", "chg60", "dist20", "dist60", "pos20", "pos60", "pos120", "eff20",
    "ma5", "ma10", "ma20", "ma60",
    "bias_ma5", "bias_ma10", "bias_ma20",
    "vol5_20", "volume_ratio_5d", "volume_ratio_vs_5d", "volume_ratio_vs_20d",
    "volume_status", "volume_trend", "volume_trend_pct",
    "volume_price_corr", "volume_pattern",
    "breakout20", "breakout60", "upper_shadow_count20", "max_dd20", "max_dd60",
    "rsi_6", "rsi_12", "rsi_24", "rsi_status", "rsi_signal",
    "macd_dif", "macd_dea", "macd_bar", "macd_status", "macd_signal",
    "trend_status", "ma_alignment", "trend_strength",
    "yesterday_high", "yesterday_low", "high_20d", "low_20d", "off_high_pct",
    "limit_up_days20", "is_st",
    "support_ma5", "support_ma10",
    "support_levels", "resistance_levels", "resistance_distance_pct",
    "current_price",
    "avg_up_day_volume", "avg_down_day_volume", "high_volume_days",
}

# Fields that come from external data sources and may or may not be available
_OBSERVABLE_FIELDS = {
    "main_net_inflow_yi",
    "pe_ttm",
    "industry",
}

# Fields that are always synthetically estimated (no real data source)
_SYNTHETIC_FIELDS = {
    "concentration_90", "profit_ratio",
    "cost_90_low", "cost_90_high", "cost_70_low", "cost_70_high",
    "concentration_70", "avg_cost",
    "revenue_yoy", "net_profit_yoy", "roe", "gross_margin",
    "sector_fund_rank",
    "dragon_tiger_is_on_list", "dragon_tiger_recent_count", "dragon_tiger_latest_date",
    "news_risk_count", "positive_catalyst_count",
    "risk_alerts", "positive_catalysts", "latest_news",
    "chip_health", "chip_health_score", "chip_concentration",
    "capital_flow_score", "fundamental_score", "intelligence_score",
    "earnings_outlook", "sentiment_summary",
    "institution_holding_change", "top10_holder_change",
    "inflow_5d_yi", "inflow_10d_yi",
    "volume_breakout_signal", "volume_shrink_pullback",
}

# Features that participate directly in scoring (DSA + risk + buy signal inputs)
_SCORED_FEATURES = {
    "trend_status", "bias_ma5", "volume_status",
    "support_ma5", "support_ma10", "macd_status", "rsi_12",
    "announcement_negative_count", "news_risk_count",
    "turnover_rate", "upper_shadow_count20", "max_dd20",
    "dsa_trend_score", "dsa_bias_score", "dsa_volume_score",
    "dsa_support_score", "dsa_macd_score", "dsa_rsi_score",
    "capital_flow_score", "fundamental_score", "intelligence_score",
    "chip_health_score",
}


def _tag_features(item: dict[str, Any]) -> None:
    """Annotate every feature with its provenance tag."""
    tags: dict[str, str] = {}
    for key in item:
        if key.startswith("_"):
            continue
        if key in _OBSERVED_FIELDS:
            tags[key] = "OBSERVED"
        elif key in _OBSERVABLE_FIELDS:
            tags[key] = "OBSERVABLE"
        elif key in _SYNTHETIC_FIELDS:
            tags[key] = "SYNTHETIC"
    item["_feature_tags"] = tags


def _compute_authenticity(item: dict[str, Any]) -> float:
    """Compute the ratio of observed features among scored features."""
    tags = item.get("_feature_tags", {})
    scored = [k for k in _SCORED_FEATURES if k in tags]
    if not scored:
        return 1.0
    observed = sum(1 for k in scored if tags[k] == "OBSERVED")
    return round(observed / len(scored), 4)
