from collections.abc import Callable, Mapping
from typing import Any

from app.services.indicator_computer import bias as _bias
from app.services.shortline_factors import apply_shared_shortline_factors


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
    "多头": 12,  # 提分（原 8）：被推荐后 WR=100%（6笔 avg+4.88%）
    "空头": 0,   # 降分（原 2）：WR=0%
    "下穿零轴": 5,  # 提分（原 0）：被推荐后 WR=75%（4笔 avg+4.49%）
    "死叉": 0,
}
# 数据驱动（T3 第80轮反事实验证）：多头/下穿零轴被推荐后 WR 高，提分净 +30.60%（0 移除）


def build_features(
    stocks: list[dict[str, Any]],
    snapshot: dict[str, Any],
    dsa_weights: dict[str, float] | None = None,
    db: Any = None,
    on_progress: Callable[[int, int], None] | None = None,
    auxiliary_views: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    main_industries = set(snapshot.get("main_industries", []))
    enriched = []
    # Sprint4 §84：全市场等权指数趋势（与回测 research_runner 同口径）
    if db is not None:
        from app.services.market_trend import trend_for_live
        mkt_trend = trend_for_live(db, str(snapshot.get("trade_date") or ""), stocks)
    else:
        mkt_trend = {"mkt_eqw_ma20_ratio": 0.0, "mkt_eqw_ma60_ratio": 0.0}
    for stock in stocks:
        item = dict(stock)
        item["is_main_theme"] = item.get("industry") in main_industries and item.get("industry") != "未分类"
        item["market_sample_count"] = int(snapshot.get("sample_count") or 0)
        item["market_regime"] = snapshot.get("breadth", {}).get("regime", "未知")
        # 市场状态是策略上下文的一部分。策略若声明恐慌日禁入，必须看到与
        # 推荐卡片一致的当日广度数据，不能只依赖笼统的 regime 标签。
        item["up_count"] = int(snapshot.get("up_count") or 0)
        item["down_count"] = int(snapshot.get("down_count") or 0)
        item["limit_up_count"] = int(snapshot.get("limit_up_count") or 0)
        item["limit_down_count"] = int(snapshot.get("limit_down_count") or 0)
        item.update(mkt_trend)
        enriched.append(item)

    # 从本地辅助数据池丰富特征（涨停板/龙虎榜/新闻/资金流/概念板块）
    if db is not None:
        _enrich_with_auxiliary_data(enriched, db, snapshot, _aux_override=auxiliary_views)

    for item in enriched:
        profile = dict(item.pop("dsa_profile", {}) or {})
        _apply_dsa_context(item, profile, dsa_weights)

    apply_shared_shortline_factors(enriched, snapshot)

    total = len(enriched)
    for i, item in enumerate(enriched, start=1):
        # 科创板标识（688/689 开头，20% 涨跌停，跳空缺口风险更高）
        code = str(item.get("code") or "")
        item["is_kcb"] = code.startswith(("688", "689"))
        item["risk_score"] = _risk_score(item)
        _tag_features(item)
        item["data_authenticity"] = _compute_authenticity(item)
        if on_progress and (i == 1 or i % 50 == 0 or i == total):
            on_progress(i, total)
    return enriched


def market_regime_score(snapshot: dict[str, Any]) -> float:
    """市场环境对短线策略的友好度评分 —— 刻意「逆势立场」，返回值越高门槛越宽松。

    因果机制：函数返回越高 → 自适应 score_line 门槛越宽松（放行更多票）。
    本函数**刻意采用逆势立场**：弱市/震荡返高分(宽松)，强牛返低分(严格)。

    ⚠️ 关键陷阱（第185轮实证 round-185 / 教训库 [T5] market_regime_score 校准倒置）：
    观测胜率随市场走强**单调上升**（LIVE formal 384 笔：弱市 up_ratio<0.35 约 71.6%
    → 强牛 >=0.65 约 96.4%）。但强牛高胜率含**选择偏差**——严格门槛只放行最优选手，
    观测值被门槛上偏，**并不反映强牛环境本身更友好**。

    ❌ 禁止据此"修正校准"去翻转函数（让强牛返高分）：翻转会放行更差票、D 项反而恶化
    （过拟合，lessons 已锁）。故本函数保持逆势：弱市(<0.35)→82(宽松)、强牛(>=0.65)→32(严格)。

    ✅ self_check._check_market_regime_calibration 运行时校验「强牛返分 < 弱市返分」不变量，
    防每轮误判循环"修正"导致 D 项退化（纯函数、不碰 DB、单测锁定）。
    """
    sample = max(1, int(snapshot.get("sample_count") or 1))
    up_ratio = float(snapshot.get("up_count") or 0) / sample
    if sample < 20:
        return 45.0
    if sample < 100:
        return 55.0
    # 弱势/震荡市场友好（短线策略最佳环境）
    if up_ratio < 0.35:
        return 82.0
    if up_ratio < 0.50:
        return 72.0
    # 偏多/强牛市场不友好（追高风险）
    if up_ratio < 0.65:
        return 42.0
    return 32.0


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
    # 筹码套牢盘风险：价格低于平均成本时抛压大，胜率从 61% 降至 45%
    price = _num(stock.get("price") or stock.get("close"))
    avg_cost = _num(stock.get("avg_cost"))
    if price > 0 and avg_cost > 0:
        cost_ratio = price / avg_cost
        if cost_ratio < 0.95:
            score += 20  # 深度套牢，强抛压
        elif cost_ratio < 1.00:
            score += 10  # 浅套牢，解套抛压
    if stock.get("pledge_ratio", 0) > 30:
        score += 15
    if stock.get("has_upcoming_float"):
        score += 10
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
    item["volume_pattern"] = str(profile.get("volume_pattern", "中性"))

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

    macd_status = str(profile.get("macd_status", "未知"))
    macd_dif = _num(profile.get("macd_dif"))
    macd_dea = _num(profile.get("macd_dea"))
    macd_bar = _num(profile.get("macd_bar"))
    item["macd_status"] = macd_status
    item["macd_dif"] = macd_dif
    item["macd_dea"] = macd_dea
    item["macd_bar"] = macd_bar
    item["macd_signal"] = str(profile.get("macd_signal", ""))

    rsi_12 = _num(profile.get("rsi_12"), 50.0)
    # RSI 有效范围 [1, 99]；0 表示上游计算异常（数据不足/数据源 None 被 _f 转 0），
    # 用默认值 50.0。rsi_6/rsi_24 同口径守卫——2026-07-07 Tushare stk_factor 批量
    # 返回空 RSI，_f(None,0.0) 写入 daily_kbars 为 0.0，曾使 rsi_6=0 漏网（rsi_12
    # 有守卫被清洗成 50，rsi_6 无守卫保留 0.0），污染特征快照。
    if rsi_12 < 1 or rsi_12 > 99:
        rsi_12 = 50.0
    rsi_6 = _num(profile.get("rsi_6"), min(95, max(8, rsi_12 + 4)))
    if rsi_6 < 1 or rsi_6 > 99:
        rsi_6 = min(95, max(8, rsi_12 + 4))
    rsi_24 = _num(profile.get("rsi_24"), min(90, max(12, rsi_12 - 3)))
    if rsi_24 < 1 or rsi_24 > 99:
        rsi_24 = min(90, max(12, rsi_12 - 3))
    item["rsi_6"] = rsi_6
    item["rsi_12"] = rsi_12
    item["rsi_24"] = rsi_24
    item["rsi_status"] = str(profile.get("rsi_status", "未知"))
    item["rsi_signal"] = str(profile.get("rsi_signal", ""))

    _apply_context_blocks(item, profile)
    _apply_dsa_scores(item, dsa_weights)


def _apply_context_blocks(item: dict[str, Any], profile: dict[str, Any]) -> None:
    gaps = set((item.get("data_quality") or {}).get("missing_fields", []))
    strict_real = item.get("data_source") == "real_market"

    # 数据可用性标记：避免把 strict_real 模式下的默认值误解为真实 0
    item["has_real_capital_flow"] = strict_real and "main_net_inflow" not in gaps
    item["has_real_valuation"] = strict_real and "valuation" not in gaps
    item["has_real_fundamental"] = bool(item.get("has_real_fundamental"))
    item["has_real_news"] = strict_real and "news" not in gaps

    item["main_net_inflow_yi"] = _num(
        profile.get("main_net_inflow_yi", item.get("main_net_inflow_yi")),
        0.0 if strict_real else _default_main_inflow(item),
    )
    item["inflow_5d_yi"] = _num(
        profile.get("inflow_5d_yi", item.get("inflow_5d_yi")),
        item["main_net_inflow_yi"] if strict_real else round(item["main_net_inflow_yi"] * 2.2, 2),
    )
    item["inflow_10d_yi"] = _num(
        profile.get("inflow_10d_yi", item.get("inflow_10d_yi")),
        item["main_net_inflow_yi"] if strict_real else round(item["main_net_inflow_yi"] * 3.4, 2),
    )
    item["sector_fund_rank"] = int(_num(profile.get("sector_fund_rank", item.get("sector_fund_rank")), 99 if strict_real else 5 if item.get("is_main_theme") else 26))
    item["capital_flow_score"] = _capital_flow_score(item)

    # ── 筹码分布真实数据映射（cyq_perf） ──
    # 如果 stocks 字典中有 chip_cost_50 / chip_winner_rate（从 daily_kbars 加载），则映射到 SYNTHETIC 字段
    chip_cost_50 = _num(item.get("chip_cost_50"))
    chip_cost_15 = _num(item.get("chip_cost_15"))
    chip_cost_85 = _num(item.get("chip_cost_85"))
    chip_weight_avg = _num(item.get("chip_weight_avg"))
    chip_winner_rate = _num(item.get("chip_winner_rate"))
    has_real_chip = chip_cost_50 > 0 or chip_weight_avg > 0

    if has_real_chip:
        # 用真实筹码数据替换估算值
        profile["avg_cost"] = chip_weight_avg if chip_weight_avg > 0 else chip_cost_50
        if chip_cost_15 > 0 and chip_cost_85 > 0:
            profile["cost_90_low"] = chip_cost_15
            profile["cost_90_high"] = chip_cost_85
            # 15–85分位相对带宽% = (85%分位成本 - 15%分位成本) / 15%分位成本 * 100
            # 这覆盖中间70%的筹码分布（非90%）。历史字段名 concentration_90 是误称。
            # ⚠️ 数值越大 = 成本分布越宽 = 筹码越分散；窄带宽对应小数值。
            # 单峰密集对应窄带宽（小数值）；策略层（chip-single-peak-breakout）按带宽解读。
            # 两个分位点不足以判断峰数；上方套牢比例还需现价相对成本分布的位置。
            profile["concentration_90"] = round((chip_cost_85 - chip_cost_15) / chip_cost_15 * 100 if chip_cost_15 > 0 else 22.0, 2)
            # 70%成本区间用 50% 为中心估算
            if chip_cost_50 > 0:
                profile["cost_70_low"] = round(chip_cost_50 * 0.96, 2)
                profile["cost_70_high"] = round(chip_cost_50 * 1.04, 2)
                profile["concentration_70"] = 8.0
        if chip_winner_rate > 0:
            profile["profit_ratio"] = chip_winner_rate
    item["has_real_chip_data"] = has_real_chip

    item["profit_ratio"] = _num(profile.get("profit_ratio", item.get("profit_ratio")), 50.0 if strict_real else _default_profit_ratio(item))
    item["avg_cost"] = _num(profile.get("avg_cost", item.get("avg_cost")), round(_num(item.get("price")) * 0.94, 2))
    item["cost_90_low"] = _num(profile.get("cost_90_low", item.get("cost_90_low")), round(item["avg_cost"] * 0.92, 2))
    item["cost_90_high"] = _num(profile.get("cost_90_high", item.get("cost_90_high")), round(item["avg_cost"] * 1.08, 2))
    item["concentration_90"] = _num(profile.get("concentration_90", item.get("concentration_90")), 13.5 if item.get("is_main_theme") else 22.0)
    item["cost_70_low"] = _num(profile.get("cost_70_low", item.get("cost_70_low")), round(item["avg_cost"] * 0.96, 2))
    item["cost_70_high"] = _num(profile.get("cost_70_high", item.get("cost_70_high")), round(item["avg_cost"] * 1.04, 2))
    item["concentration_70"] = _num(profile.get("concentration_70", item.get("concentration_70")), 8.5 if item.get("is_main_theme") else 15.0)
    # chip_concentration 实为 15–85分位相对带宽%（concentration_90 别名，历史误称）。
    # R4 修正：越大越分散（非"集中度"），15–85分位覆盖中间70%分布（非90%），
    # 两个分位点不足以判断峰数或套牢程度。2026-09-13 深审 A3-1 修正方向。
    item["chip_concentration"] = item["concentration_90"]
    item["chip_health"] = str(profile.get("chip_health") or _chip_health(item))
    item["chip_health_score"] = _chip_health_score(item)

    item["revenue_yoy"] = profile.get("revenue_yoy", item.get("revenue_yoy"))
    item["net_profit_yoy"] = profile.get("net_profit_yoy", item.get("net_profit_yoy"))
    item["roe"] = profile.get("roe", item.get("roe"))
    item["gross_margin"] = profile.get("gross_margin", item.get("gross_margin"))
    item["pe_ttm"] = _num(profile.get("pe_ttm", item.get("pe_ttm")), 0.0 if strict_real else 32.0 if item.get("is_main_theme") else 26.0)
    item["earnings_outlook"] = str(profile.get("earnings_outlook") or ("未接入真实财务增强数据" if strict_real else "暂无重大负面业绩信号"))
    item["fundamental_score"] = _fundamental_score(item)
    item["institution_holding_change"] = _num(profile.get("institution_holding_change", item.get("institution_holding_change")), 0.0)
    item["top10_holder_change"] = _num(profile.get("top10_holder_change", item.get("top10_holder_change")), 0.0)

    risk_alerts = list(profile.get("risk_alerts") or item.get("risk_alerts") or [])
    positive_catalysts = list(profile.get("positive_catalysts") or item.get("positive_catalysts") or [])
    item["latest_news"] = str(profile.get("latest_news") or item.get("latest_news") or ("未接入实时新闻源" if strict_real else "暂无重大未处理新闻"))
    item["risk_alerts"] = risk_alerts
    item["positive_catalysts"] = positive_catalysts
    item["news_risk_count"] = int(_num(profile.get("news_risk_count", item.get("news_risk_count")), len(risk_alerts)))
    item["positive_catalyst_count"] = int(_num(profile.get("positive_catalyst_count", item.get("positive_catalyst_count")), len(positive_catalysts)))
    item["sentiment_summary"] = str(profile.get("sentiment_summary") or _sentiment_summary(item))
    item["intelligence_score"] = _intelligence_score(item)
    if strict_real:
        if "main_net_inflow" in gaps:
            item["capital_flow_score"] = min(item["capital_flow_score"], 55)
        if "valuation" in gaps:
            item["fundamental_score"] = min(item["fundamental_score"], 50)
        item["intelligence_score"] = min(item["intelligence_score"], 50)

    item["dragon_tiger_is_on_list"] = _bool(profile.get("dragon_tiger_is_on_list", item.get("dragon_tiger_is_on_list")), False)
    item["dragon_tiger_recent_count"] = int(_num(profile.get("dragon_tiger_recent_count", item.get("dragon_tiger_recent_count")), 0))
    item["dragon_tiger_latest_date"] = profile.get("dragon_tiger_latest_date", item.get("dragon_tiger_latest_date"))

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

    # Use calibrated 基础短线策略 weights if available, otherwise equal weight (simple sum)
    w = dsa_weights or {}
    w_trend = w.get("dsa_w_trend", 1.0)
    w_bias = w.get("dsa_w_bias", 1.0)
    w_volume = w.get("dsa_w_volume", 1.0)
    w_support = w.get("dsa_w_support", 1.0)
    w_macd = w.get("dsa_w_macd", 1.0)
    w_rsi = w.get("dsa_w_rsi", 1.0)

    # 归一化权重：确保加权和仍在 0-100 范围内
    # 各维度满分：trend=30, bias=20, volume=15, support=10, macd=15, rsi=10
    max_possible = (30 * w_trend + 20 * w_bias + 15 * w_volume + 10 * w_support + 15 * w_macd + 10 * w_rsi)
    norm = 100.0 / max_possible if max_possible > 0 else 1.0

    item["dsa_signal_score"] = int(
        (item["dsa_trend_score"] * w_trend
        + item["dsa_bias_score"] * w_bias
        + item["dsa_volume_score"] * w_volume
        + item["dsa_support_score"] * w_support
        + item["dsa_macd_score"] * w_macd
        + item["dsa_rsi_score"] * w_rsi) * norm
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


def _bias_score(bias_ma5: float) -> int:
    # 数据驱动（2026-07-04 去重 282 笔 + 反事实验证 T2 第77轮）：
    # 高 bias = 强势股 = 强者恒强（趋势跟踪市场），应高分
    #   bias > 5:  WR=77.8-100% → 20 分
    #   3 < bias <= 5: WR=66.7% → 18 分
    #   1.5 < bias <= 3: WR=59.0% → 16 分
    #   -2 <= bias <= 1.5: 整体 WR=49.4%，但被推荐后 WR=63.3%（安全区间）→ 20 分（保持旧值，反事实验证降分误杀盈利股）
    #   -7 <= bias < -2: WR=38.5-47.1% → 8 分
    #   bias < -7: 弱势 → 4 分
    # 旧逻辑错误：高 bias>8 给 2 分（实际 WR=100%）；新逻辑修正高 bias 加分
    # 反事实验证（T2 第77轮）：对 bias[-2,1.5] 降分会移除 30+ 笔 WR=63.3% 的盈利推荐（净 -39%），
    #   保持 20 分避免误杀；只对高 bias 加分
    if bias_ma5 > 5:
        return 20
    if bias_ma5 > 3:
        return 18
    if bias_ma5 > 1.5:
        return 16
    if bias_ma5 >= -2:
        return 20  # 保持旧值：被推荐后 WR=63.3%，降分误杀
    if bias_ma5 >= -7:
        return 8
    return 4


def _rsi_score(rsi: float) -> int:
    # 数据驱动（2026-07-04 去重 282 笔 + 反事实验证 T3 第78轮）：
    # rsi[40,50) 温和偏强，被推荐后 WR=100%（9笔 avg+6.70%）→ 提分到 10
    # rsi<30 超卖，整体 WR=40% 但被推荐后 WR=75%（推荐线已筛选）→ 保持 10
    # rsi[50,70] 中性偏强，整体 WR=54.2% → 保持 8
    # rsi>70 超买，被推荐后 WR=25% avg-0.88% → 0 分（确实差）
    # 反事实验证：rsi[40,50) 提分净 +4.24%（0 移除，纯增量）
    if rsi < 30:
        return 10
    if 40 <= rsi < 50:
        return 10  # 提分（原 5）：被推荐后 WR=100%
    if 50 <= rsi <= 70:
        return 8
    if 30 <= rsi < 40:
        return 3
    return 0


def _capital_flow_score(item: dict[str, Any]) -> float:
    """资金流向评分（0-100）。

    修正历史问题（2026-07-28）：
    - inflow_5d_yi 字段不存在（view 提供的是 cumulative_inflow_5d_yi），旧代码恒走 -10 分支。
    - sector_fund_rank 字段不存在（view 里无此字段），旧代码默认 99 恒走 -8 分支。
    - 两项死因子共 -18 分噪声，导致 capital_flow_score 只有 32/46 两值且无区分度。
    - main_net_inflow_ratio 已废弃（旧公式垃圾 + 单位错致异常值），置 None。

    修正后公式：
    - 主力净流入当日（main_net_inflow_yi）±18 分（量级-6~+7 亿 → [-18,+21]）
    - 累计5日流入（cumulative_inflow_5d_yi）±14 分（>0 给 +14，<=0 给 -10）
    - 去掉 sector_fund_rank（从未工作过，恒 -8 分噪声）
    """
    score = 50.0
    score += min(22, max(-18, _num(item.get("main_net_inflow_yi")) * 3))
    score += 14 if _num(item.get("cumulative_inflow_5d_yi")) > 0 else -10
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
    # 任一关键字段有真实值才评分；否则返回中性 55（诚实表达"无数据"）
    has_any = any(
        item.get(k) is not None and item.get(k) != ""
        for k in ("revenue_yoy", "net_profit_yoy", "roe", "gross_margin")
    )
    if not has_any:
        return 55.0
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
    if item.get("positive_catalyst_count", 0) > 0 and item.get("is_main_theme"):
        return "主线题材叠加正向催化"
    if item.get("is_main_theme"):
        return "处于主线行业，情绪承接较好"
    return "非主线方向，情绪确认不足"


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
    main_bonus = 1.2 if item.get("is_main_theme") else 0.4
    return round(_num(item.get("amount_yi")) * 0.05 * direction * main_bonus, 2)


def _default_profit_ratio(item: dict[str, Any]) -> float:
    return round(min(92, max(8, 45 + _num(item.get("pos20")) * 0.35 + _num(item.get("chg20")) * 0.25)), 2)


def _ma_from_bias(price: float, bias_pct: float) -> float:
    return price / (1 + bias_pct / 100) if (1 + bias_pct / 100) else price


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
    "yesterday_high", "yesterday_low", "platform_high_20d", "platform_high_60d",
    "high_20d", "low_20d", "off_high_pct", "off_high60_pct",
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
    "revenue_yoy", "net_profit_yoy", "roe", "gross_margin",
}

# Fields that come from external data sources and may or may not be available
# Now including fields from auxiliary data sources (limit_up, dragon_tiger, news, capital_flow)
_OBSERVABLE_FIELDS.update({
    "dragon_tiger_is_on_list", "dragon_tiger_recent_count", "dragon_tiger_latest_date",
    "dragon_tiger_buy_amount_yi", "dragon_tiger_sell_amount_yi", "dragon_tiger_net_amount_yi",
    "dragon_tiger_buy_seat_count", "dragon_tiger_institution_net_buy_yi", "dragon_tiger_net_buy_ratio",
    "news_risk_count", "positive_catalyst_count",
    "risk_alerts", "positive_catalysts", "latest_news",
    "announcement_negative_count",
    "inflow_5d_yi", "inflow_10d_yi",
    "is_limit_up", "limit_up_consecutive_days", "limit_up_seal_time", "limit_up_reason",
    "limit_up_first_time", "limit_up_last_time", "limit_up_open_times",
    "limit_up_up_stat", "limit_up_limit_amount_yi", "limit_up_fd_amount_yi",
    "limit_up_turnover_ratio",
    "concept_sector_name", "concept_sector_heat_rank",
    "super_large_net_inflow_yi", "large_net_inflow_yi", "medium_net_inflow_yi", "small_net_inflow_yi",
    "main_net_inflow_ratio", "cumulative_inflow_3d_yi",
    "is_main_theme", "market_sentiment_phase",
    "limit_up_overheat_level", "sector_ladder_level",
    "sector_ladder_limit_up_count", "sector_ladder_height",
    "capital_resonance_signal", "capital_resonance_source",
    "is_20cm_stock", "twenty_cm_risk_gate", "twenty_cm_risk_reason",
    "is_shortline_leader_candidate", "dragon_tiger_buy_dispersion",
    "dragon_tiger_inst_buy_ratio", "shortline_factor_explanations",
    # 新增辅助数据源字段
    "holder_num", "holder_num_change_pct", "holder_num_decreasing",
    "holder_trade_count", "holder_trade_increase_count", "holder_trade_decrease_count", "holder_trade_net_ratio",
    "survey_count", "survey_fund_visitors", "survey_latest_date",
    "block_trade_avg_discount_rate",
    "auction_volume_ratio", "auction_turnover_rate", "auction_price", "auction_pre_close",
    "is_broken_limit_up", "broken_limit_up_open_times", "broken_limit_up_up_stat",
    "has_real_chip_data",
    # 筹码字段（有 cyq_perf 真实数据源时为 OBSERVED，无数据时 _tag_features 经 chip 类别判 FALLBACK）
    "avg_cost", "profit_ratio", "concentration_90",
    "cost_90_low", "cost_90_high", "chip_concentration",
})

# 辅助字段 → 数据源类别映射。missing_fields 用类别名（news/announcements/dragon_tiger/...），
# 而 _tag_features 按字段名匹配，两者命名空间不一致会导致辅助字段永远被标 OBSERVED，
# 使 data_authenticity 高估、真实性折扣不触发。这里补上映射让辅助字段正确识别缺失。
_FIELD_TO_MISSING_CATEGORY: dict[str, str] = {}
for _f in (
    "news_risk_count", "positive_catalyst_count", "risk_alerts", "positive_catalysts",
    "latest_news", "announcement_negative_count",
):
    _FIELD_TO_MISSING_CATEGORY[_f] = "news"  # news 与 announcements 同源拉取，缺一即都缺
for _f in (
    "dragon_tiger_is_on_list", "dragon_tiger_recent_count", "dragon_tiger_latest_date",
    "dragon_tiger_buy_amount_yi", "dragon_tiger_sell_amount_yi", "dragon_tiger_net_amount_yi",
    "dragon_tiger_buy_seat_count", "dragon_tiger_institution_net_buy_yi", "dragon_tiger_net_buy_ratio",
    "dragon_tiger_buy_dispersion", "dragon_tiger_inst_buy_ratio",
):
    _FIELD_TO_MISSING_CATEGORY[_f] = "dragon_tiger"
for _f in (
    "concept_sector_name", "concept_sector_heat_rank", "is_main_theme",
    "sector_ladder_level", "sector_ladder_limit_up_count", "sector_ladder_height",
):
    _FIELD_TO_MISSING_CATEGORY[_f] = "theme_tags"
for _f in (
    "main_net_inflow_yi", "inflow_5d_yi", "inflow_10d_yi",
    "super_large_net_inflow_yi", "large_net_inflow_yi", "medium_net_inflow_yi",
    "small_net_inflow_yi", "main_net_inflow_ratio", "cumulative_inflow_3d_yi",
):
    _FIELD_TO_MISSING_CATEGORY[_f] = "capital_flow"
for _f in ("revenue_yoy", "net_profit_yoy", "roe", "gross_margin"):
    _FIELD_TO_MISSING_CATEGORY[_f] = "fundamental"
# 筹码字段 → chip 类别（有 cyq_perf 时为 OBSERVED，无时退化为 ESTIMATED）
for _f in ("avg_cost", "profit_ratio", "concentration_90", "cost_90_low", "cost_90_high", "chip_concentration"):
    _FIELD_TO_MISSING_CATEGORY[_f] = "chip"
# 股东人数 → holder_number 类别
for _f in ("holder_num", "holder_num_change_pct", "holder_num_decreasing"):
    _FIELD_TO_MISSING_CATEGORY[_f] = "holder_number"
# 股东增减持 → holder_trade 类别
for _f in ("holder_trade_count", "holder_trade_increase_count", "holder_trade_decrease_count", "holder_trade_net_ratio"):
    _FIELD_TO_MISSING_CATEGORY[_f] = "holder_trade"
# 机构调研 → survey 类别
for _f in ("survey_count", "survey_fund_visitors", "survey_latest_date"):
    _FIELD_TO_MISSING_CATEGORY[_f] = "survey"
# 大宗交易折价率 → block_trade 类别
_FIELD_TO_MISSING_CATEGORY["block_trade_avg_discount_rate"] = "block_trade"
for _f in ("auction_volume_ratio", "auction_turnover_rate", "auction_price", "auction_pre_close"):
    _FIELD_TO_MISSING_CATEGORY[_f] = "auction"

# Fields that are always synthetically estimated (no real data source)
# 注意：avg_cost/profit_ratio/concentration_90 等在有 cyq_perf 数据时已映射为真实值（_OBSERVABLE_FIELDS），
# 但无 cyq_perf 时仍退化为估算值，因此保留在 SYNTHETIC 中作为降级标记。
_SYNTHETIC_FIELDS = {
    "cost_70_low", "cost_70_high",
    "concentration_70",
    "sector_fund_rank",
    "chip_health", "chip_health_score",
    "earnings_outlook", "sentiment_summary",
    "top10holder_change",
    "volume_breakout_signal", "volume_shrink_pullback",
}

# Composite scores may be computed from real observable inputs (quotes, kline,
# auxiliary flow/news/fundamental data). Treating them as intrinsically
# synthetic made local-pool runs look less authentic even when the underlying
# evidence was real; quality gates should inspect source/missing fields instead.
_DERIVED_SCORE_FIELDS = {
    "capital_flow_score", "fundamental_score", "intelligence_score",
    "dsa_trend_score", "dsa_bias_score", "dsa_volume_score",
    "dsa_support_score", "dsa_macd_score", "dsa_rsi_score",
    "chip_health_score", "dsa_signal_score", "risk_score",
    "limit_up_overheat_score", "sector_ladder_completeness_score",
    "capital_resonance_score", "twenty_cm_risk_score",
    "shortline_leader_score",
}

# Features that participate directly in scoring (基础短线策略 + risk + shared strategy factors)
_SCORED_FEATURES = {
    "trend_status", "bias_ma5", "volume_status",
    "support_ma5", "support_ma10", "macd_status", "rsi_12",
    "announcement_negative_count", "news_risk_count",
    "turnover_rate", "upper_shadow_count20", "max_dd20", "off_high_pct", "off_high60_pct",
    "volume_shrink_pullback", "volume_breakout_signal",
    "limit_up_consecutive_days", "limit_up_open_times",
    "limit_up_fd_amount_yi", "limit_up_turnover_ratio",
    "primary_excess20", "primary_excess60", "industry_positive_excess20_count",
    "dsa_trend_score", "dsa_bias_score", "dsa_volume_score",
    "dsa_support_score", "dsa_macd_score", "dsa_rsi_score",
    "capital_flow_score", "fundamental_score", "intelligence_score",
    "chip_health_score", "dsa_signal_score", "risk_score",
    "limit_up_overheat_score", "sector_ladder_completeness_score",
    "capital_resonance_score", "twenty_cm_risk_score",
    "shortline_leader_score",
}


def _enrich_with_auxiliary_data(stocks: list[dict[str, Any]], db: Any, snapshot: dict[str, Any], _aux_override: dict | None = None) -> None:
    """从本地辅助数据池加载真实数据，覆盖 SYNTHETIC 默认值。"""
    from app.services.auxiliary.application import load_auxiliary_feature_views

    codes = [s["code"] for s in stocks]
    if not codes:
        return
    trade_date = str(snapshot.get("trade_date") or "")
    aux = _aux_override if _aux_override is not None else load_auxiliary_feature_views(
        db,
        codes,
        trade_date=trade_date,
    )

    limit_up_map = aux.get("limit_up", {})
    concept_map = aux.get("concept_sectors", {})
    flow_map = aux.get("capital_flows", {})
    dt_map = aux.get("dragon_tigers", {})
    news_map = aux.get("news", {})
    auction_map = aux.get("auctions", {})
    pledge_map = aux.get("pledges", {})
    broker_rec_map = aux.get("broker_recs", {})
    float_upcoming_map = aux.get("share_floats_upcoming", {})
    margin_detail_map = aux.get("margin_details", {})
    block_trade_map = aux.get("block_trades", {})
    hsgt_top10_map = aux.get("hsgt_top10", {})
    hsgt_flow_data = aux.get("hsgt_flow")
    fina_map = aux.get("finas", {})
    broken_limit_up_map = aux.get("broken_limit_up", {})
    holder_number_map = aux.get("holder_numbers", {})
    holder_trade_map = aux.get("holder_trades", {})
    survey_map = aux.get("surveys", {})
    coverage = aux.get("coverage", {}) or {}
    holder_trade_snapshot_ok = _event_snapshot_ok(coverage, "holder_trade")
    # surveys 只有显式加载（aux 带 "surveys" 键，且 coverage 未标错误/停更）才算可用
    survey_snapshot_ok = "surveys" in aux and _event_snapshot_ok(coverage, "institutional_survey", default=True)
    # 公告是 7 个按日分区；只有整体不可用（有错误且 0 行）才不认“查过为空”，个别节假日分区缺失不影响
    _news_cov = coverage.get("news_announcement") if isinstance(coverage, Mapping) else None
    news_snapshot_ok = not (
        isinstance(_news_cov, Mapping) and _news_cov.get("errors") and not int(_news_cov.get("record_count") or 0)
    )
    auxiliary_trade_date = str(aux.get("as_of_trade_date") or trade_date)
    audit = (snapshot.get("breadth") or {}).get("auxiliary_data_audit") or {}
    checked_news_codes = _checked_codes_from_audit(audit.get("news"))
    checked_capital_flow_codes = _checked_codes_from_audit(audit.get("capital_flows"))
    dragon_tiger_covered = _dragon_tiger_trade_date_covered(
        coverage,
        audit.get("dragon_tiger"),
        auxiliary_trade_date,
    )

    for stock in stocks:
        code = stock["code"]
        data_quality = stock.setdefault("data_quality", {})
        missing_fields = set(data_quality.get("missing_fields") or [])
        optional_missing_fields = set(data_quality.get("optional_missing_fields") or [])
        fallback_fields = set(data_quality.get("fallback_fields") or [])
        profile = stock.get("dsa_profile", {})
        if not isinstance(profile, dict):
            profile = {}

        # ── 涨停板特征 ──
        lu = limit_up_map.get(code)
        if lu:
            stock["is_limit_up"] = True
            stock["limit_up_consecutive_days"] = int(lu.get("limit_times") or lu.get("consecutive_days") or 1)
            stock["limit_up_seal_time"] = lu.get("limit_up_time") or lu.get("first_time")
            stock["limit_up_first_time"] = lu.get("first_time")
            stock["limit_up_last_time"] = lu.get("last_time")
            stock["limit_up_open_times"] = int(lu.get("open_times") or 0)
            stock["limit_up_up_stat"] = lu.get("up_stat")
            stock["limit_up_limit_amount_yi"] = lu.get("limit_amount_yi")
            stock["limit_up_fd_amount_yi"] = lu.get("fd_amount_yi")
            stock["limit_up_turnover_ratio"] = lu.get("turnover_ratio")
            stock["limit_up_reason"] = lu.get("limit_up_reason")
        else:
            stock["is_limit_up"] = False
            stock["limit_up_consecutive_days"] = 0
            stock["limit_up_open_times"] = 0

        # ── 龙虎榜特征 ──
        dt = dt_map.get(code)
        if dt:
            stock["dragon_tiger_is_on_list"] = True
            stock["dragon_tiger_recent_count"] = 1
            stock["dragon_tiger_latest_date"] = dt.get("trade_date")
            stock["dragon_tiger_buy_amount_yi"] = dt.get("buy_amount_yi", 0)
            stock["dragon_tiger_sell_amount_yi"] = dt.get("sell_amount_yi", 0)
            net_amount_yi = _num(dt.get("net_amount_yi", 0))
            stock["dragon_tiger_net_amount_yi"] = net_amount_yi
            # 2026-09-13 修复（深审 A3-2 机构净买比例退化为净买方向）：此前把总净买额
            # 赋给 dragon_tiger_institution_net_buy_yi 作为"聪明钱代理"，使
            # DragonTigerFactor 算出的 dragon_tiger_inst_buy_ratio 在净买时恒为 +1、
            # 净卖时恒为 -1——陈小群 inst>=0.5/+0.7 两规则在净买时合计 +10、龙虎榜
            # "机构净买确认"均被净买方向冒充机构身份触发，无任何席位区分力。
            # dragon_tiger 表无机构细分列，真实机构净买需接入 top_inst 席位明细
            # （机构专用席位）后才可赋值；接入前本字段保持缺失（_num 缺省 0，
            # inst_buy_ratio 输出 0，机构类规则不再生效）。
            # 聪明钱方向的诚实代理仍是 dragon_tiger_net_buy_ratio（净买占成交额比例）。
            # 聪明钱强度 = 龙虎榜净买占当日成交额比例（已回测验证：>=5% -> WR 85.6% / avg+4.82%）。
            amount_yi = _num(stock.get("amount_yi"))
            stock["dragon_tiger_net_buy_ratio"] = (
                round(min(1.0, max(-1.0, net_amount_yi / amount_yi)), 4) if amount_yi else 0.0
            )
            # R2 修复：显式清理旧机构代理字段（若调用方已携带污染输入）
            stock.pop("dragon_tiger_institution_net_buy_yi", None)
            missing_fields.discard("dragon_tiger")
            data_quality["dragon_tiger_checked_trade_date"] = dt.get("trade_date") or auxiliary_trade_date
        else:
            stock["dragon_tiger_is_on_list"] = False
            stock["dragon_tiger_recent_count"] = 0
            if dragon_tiger_covered:
                missing_fields.discard("dragon_tiger")
                data_quality["dragon_tiger_checked_empty"] = True
                data_quality["dragon_tiger_checked_trade_date"] = auxiliary_trade_date
            else:
                missing_fields.add("dragon_tiger")

        # ── 新闻/公告特征 ──
        news_items = news_map.get(code, [])
        risk_items = [n for n in news_items if n.get("is_risk_warning")]
        positive_items = [n for n in news_items if n.get("sentiment") == "positive"]
        stock["news_risk_count"] = len(risk_items)
        stock["positive_catalyst_count"] = len(positive_items)
        if risk_items:
            stock["risk_alerts"] = [n["title"] for n in risk_items[:3]]
        if positive_items:
            stock["positive_catalysts"] = [n["title"] for n in positive_items[:3]]
        if news_items:
            stock["latest_news"] = news_items[0].get("title", "")
            missing_fields.discard("news")
            missing_fields.discard("announcements")
            data_quality["news_checked"] = True
            data_quality["announcements_checked"] = True
        else:
            if code in checked_news_codes and news_snapshot_ok:
                missing_fields.discard("news")
                missing_fields.discard("announcements")
                data_quality["news_checked_empty"] = True
                data_quality["announcements_checked_empty"] = True
            else:
                missing_fields.update({"news", "announcements"})

        # ── 概念板块特征 ──
        concepts = concept_map.get(code, [])
        if concepts:
            best = min(concepts, key=lambda c: c.get("heat_rank", 999))
            theme_tags = [
                str(c.get("sector_name") or "").strip()
                for c in sorted(concepts, key=lambda c: c.get("heat_rank", 999))[:5]
                if str(c.get("sector_name") or "").strip()
            ]
            stock["concept_sector_name"] = best.get("sector_name")
            stock["concept_sector_heat_rank"] = best.get("heat_rank", 0)
            stock["theme_tags"] = theme_tags
            missing_fields.discard("theme_tags")
            data_quality["theme_tags_checked"] = True
        elif stock.get("industry") and stock.get("industry") != "未分类":
            # 行业作为概念板块降级方案
            stock["concept_sector_name"] = stock["industry"]
            stock["concept_sector_heat_rank"] = 50  # 中性热度
            stock["theme_tags"] = [stock["industry"]]
            fallback_fields.update({"concept_sector_name", "concept_sector_heat_rank", "theme_tags"})
            missing_fields.discard("theme_tags")
            data_quality["theme_tags_fallback_from_industry"] = True
        else:
            missing_fields.add("theme_tags")

        # ── 资金流向细分 ──
        cf = flow_map.get(code)
        if cf:
            stock["main_net_inflow_yi"] = cf.get("main_net_inflow_yi", 0)
            stock["super_large_net_inflow_yi"] = cf.get("super_large_net_inflow_yi", 0)
            stock["large_net_inflow_yi"] = cf.get("large_net_inflow_yi", 0)
            stock["medium_net_inflow_yi"] = cf.get("medium_net_inflow_yi", 0)
            stock["small_net_inflow_yi"] = cf.get("small_net_inflow_yi", 0)
            stock["main_net_inflow_ratio"] = cf.get("main_net_inflow_ratio", 0)
            stock["inflow_5d_yi"] = cf.get("cumulative_inflow_5d_yi", 0)
            stock["inflow_10d_yi"] = cf.get("cumulative_inflow_10d_yi", 0)
            stock["cumulative_inflow_3d_yi"] = cf.get("cumulative_inflow_3d_yi", 0)
            profile["main_net_inflow_yi"] = cf.get("main_net_inflow_yi", 0)
            missing_fields.discard("capital_flow")
            missing_fields.discard("main_net_inflow")
            data_quality["capital_flow_checked"] = True
        else:
            if code in checked_capital_flow_codes:
                missing_fields.discard("capital_flow")
                missing_fields.discard("main_net_inflow")
                data_quality["capital_flow_checked_empty"] = True
            else:
                missing_fields.add("capital_flow")

        # ── 集合竞价特征 ──
        auc = auction_map.get(code)
        if auc:
            auction_price = _num(auc.get("price"))
            auction_pre_close = _num(auc.get("pre_close"))
            stock["auction_volume_ratio"] = auc.get("volume_ratio")
            stock["auction_turnover_rate"] = auc.get("turnover_rate")
            stock["auction_price"] = auc.get("price")
            stock["auction_pre_close"] = auc.get("pre_close")
            if auction_price > 0 and auction_pre_close > 0:
                stock["auction_signal_available"] = True
                stock["auction_gap_pct"] = round(
                    (auction_price / auction_pre_close - 1.0) * 100.0,
                    4,
                )
                missing_fields.discard("auction")
                optional_missing_fields.discard("auction")
                data_quality["auction_checked"] = True
            else:
                stock["auction_signal_available"] = False
                stock["auction_gap_pct"] = None
                optional_missing_fields.add("auction")
                data_quality["auction_invalid_values"] = True
        else:
            # 缺失必须保持不可用，不能用 0 或 1.0 伪造中性竞价信号。
            stock["auction_volume_ratio"] = None
            stock["auction_turnover_rate"] = None
            stock["auction_price"] = None
            stock["auction_pre_close"] = None
            stock["auction_signal_available"] = False
            stock["auction_gap_pct"] = None
            optional_missing_fields.add("auction")

        # ── 股权质押风险 ──
        pledge = pledge_map.get(code)
        if pledge:
            stock["pledge_ratio"] = pledge.get("pledge_ratio", 0)
            stock["pledge_count"] = pledge.get("pledge_count", 0)
        else:
            stock["pledge_ratio"] = 0
            stock["pledge_count"] = 0

        # ── 券商金股共识 ──
        stock["broker_rec_count"] = broker_rec_map.get(code, 0)

        # ── 限售解禁风险 ──
        floats = float_upcoming_map.get(code, [])
        if floats:
            stock["has_upcoming_float"] = True
            stock["upcoming_float_days"] = min(
                (f.get("float_date") for f in floats), default=""
            )
            stock["upcoming_float_ratio"] = sum(
                f.get("float_ratio", 0) for f in floats
            )
        else:
            stock["has_upcoming_float"] = False
            stock["upcoming_float_ratio"] = 0

        # ── 融资融券特征 ──
        md = margin_detail_map.get(code)
        if md:
            stock["margin_rzye"] = md.get("rzye", 0)          # 融资余额(亿)
            stock["margin_rzmre"] = md.get("rzmre", 0)         # 融资买入额(亿)
            stock["margin_rzche"] = md.get("rzche", 0)         # 融资偿还额(亿)
            stock["margin_rzrqye"] = md.get("rzrqye", 0)       # 融资融券余额(亿)
            # 融资净买入 = 买入 - 偿还
            stock["margin_net_buy_yi"] = round((md.get("rzmre", 0) or 0) - (md.get("rzche", 0) or 0), 4)
        else:
            stock["margin_rzye"] = 0
            stock["margin_rzmre"] = 0
            stock["margin_rzche"] = 0
            stock["margin_rzrqye"] = 0
            stock["margin_net_buy_yi"] = 0

        # ── 大宗交易特征（含折价率） ──
        bt_list = block_trade_map.get(code, [])
        if bt_list:
            stock["block_trade_count"] = len(bt_list)
            stock["block_trade_total_yi"] = round(sum(b.get("amount_yi", 0) or 0 for b in bt_list), 4)
            # 折价率 = (成交价 / 当日收盘价 - 1) * 100，折价率高=卖方急售=看空
            close_price = _num(stock.get("price") or stock.get("close"))
            if close_price > 0:
                discount_rates = [
                    round((b.get("price", 0) or 0) / close_price - 1, 4)
                    for b in bt_list
                    if b.get("price") and b.get("price") > 0
                ]
                stock["block_trade_avg_discount_rate"] = (
                    round(sum(discount_rates) / len(discount_rates), 4) if discount_rates else 0.0
                )
            else:
                stock["block_trade_avg_discount_rate"] = 0.0
        else:
            stock["block_trade_count"] = 0
            stock["block_trade_total_yi"] = 0
            stock["block_trade_avg_discount_rate"] = 0.0

        # ── 北向持股特征 ──
        hsgt = hsgt_top10_map.get(code)
        if hsgt:
            stock["hsgt_rank"] = hsgt.get("rank", 0)
            stock["hsgt_net_buy"] = hsgt.get("buy", 0)        # 买入量(股)
        else:
            stock["hsgt_rank"] = 0
            stock["hsgt_net_buy"] = 0

        # ── 北向资金整体流向（市场级） ──
        if hsgt_flow_data:
            stock["north_money_yi"] = hsgt_flow_data.get("north_money", 0)
        else:
            stock["north_money_yi"] = 0

        # ── 财务指标特征（fina_indicator 真实数据） ──
        fina = fina_map.get(code)
        if fina:
            profile["revenue_yoy"] = fina.get("revenue_yoy")
            profile["net_profit_yoy"] = fina.get("net_profit_yoy")
            profile["roe"] = fina.get("roe")
            profile["gross_margin"] = fina.get("gross_margin")
            profile["pe_ttm"] = fina.get("pe_ttm")
            if fina.get("end_date"):
                profile["earnings_outlook"] = f"财报期{fina['end_date']}"
                profile["fina_end_date"] = fina["end_date"]
            if fina.get("ann_date"):
                profile["fina_ann_date"] = fina["ann_date"]
            has_real_fina = any(
                fina.get(k) is not None
                for k in ("revenue_yoy", "net_profit_yoy", "roe", "gross_margin")
            )
            if has_real_fina:
                missing_fields.discard("fundamental")
                data_quality["fina_checked"] = True
            else:
                missing_fields.add("fundamental")
            stock["has_real_fundamental"] = has_real_fina
        else:
            stock["has_real_fundamental"] = False
            missing_fields.add("fundamental")

        # ── 股东人数变化（筹码集中度代理） ──
        # 替换 SYNTHETIC 的 institution_holding_change：股东人数减少=机构建仓=看多
        hn = holder_number_map.get(code)
        if hn:
            stock["holder_num"] = hn.get("holder_num", 0)
            stock["holder_num_change_pct"] = hn.get("holder_num_change_pct", 0.0)
            stock["holder_num_decreasing"] = hn.get("holder_num_decreasing", False)
            # 用股东人数变化率作为机构持仓变化的代理（负值=人数减少=筹码集中）
            profile["institution_holding_change"] = -hn.get("holder_num_change_pct", 0.0)
        else:
            stock["holder_num"] = 0
            stock["holder_num_change_pct"] = 0.0
            stock["holder_num_decreasing"] = False

        # ── 股东增减持特征 ──
        # 2026-10-19：快照不可用（holder_trade 无覆盖/有错误）时报缺失（None + optional_missing_fields），
        # 不再写 0 冒充“查过无增减持”。10-09 后快照 unknown_schema 期间全市场都被写成 0。
        ht = holder_trade_map.get(code)
        if ht:
            stock["holder_trade_count"] = ht.get("holder_trade_count", 0)
            stock["holder_trade_increase_count"] = ht.get("holder_trade_increase_count", 0)
            stock["holder_trade_decrease_count"] = ht.get("holder_trade_decrease_count", 0)
            stock["holder_trade_net_ratio"] = ht.get("holder_trade_net_ratio", 0.0)
        elif holder_trade_snapshot_ok:
            stock["holder_trade_count"] = 0
            stock["holder_trade_increase_count"] = 0
            stock["holder_trade_decrease_count"] = 0
            stock["holder_trade_net_ratio"] = 0.0
        else:
            for _f in ("holder_trade_count", "holder_trade_increase_count",
                       "holder_trade_decrease_count", "holder_trade_net_ratio"):
                stock[_f] = None
            optional_missing_fields.add("holder_trade")
            data_quality["holder_trade_snapshot_unavailable"] = True

        # ── 机构调研特征 ──
        # 2026-10-19：机构调研未接入 feature views（S4 audit-only，aux 无 "surveys" 键）或本地表停更时报缺失，
        # 不再写 0 冒充“近 30 天无调研”。
        sv = survey_map.get(code)
        if sv:
            stock["survey_count"] = sv.get("survey_count", 0)
            stock["survey_fund_visitors"] = sv.get("survey_fund_visitors", 0)
            stock["survey_latest_date"] = sv.get("survey_latest_date", "")
        elif survey_snapshot_ok:
            stock["survey_count"] = 0
            stock["survey_fund_visitors"] = 0
            stock["survey_latest_date"] = ""
        else:
            stock["survey_count"] = None
            stock["survey_fund_visitors"] = None
            stock["survey_latest_date"] = None
            optional_missing_fields.add("survey")
            data_quality["survey_snapshot_unavailable"] = True

        # ── 炸板数据（市场情绪温度计，limit_type='Z'） ──
        # 炸板=曾涨停后打开，炸板率高=市场情绪转弱
        broken_lu = broken_limit_up_map.get(code)
        stock["is_broken_limit_up"] = broken_lu is not None
        if broken_lu:
            stock["broken_limit_up_open_times"] = int(broken_lu.get("open_times") or 0)
            stock["broken_limit_up_up_stat"] = broken_lu.get("up_stat")

        stock["dsa_profile"] = profile
        data_quality["missing_fields"] = sorted(missing_fields)
        data_quality["optional_missing_fields"] = sorted(optional_missing_fields)
        data_quality["fallback_fields"] = sorted(fallback_fields)

def _event_snapshot_ok(coverage: Mapping[str, Any] | Any, dataset: str, *, default: bool = False) -> bool:
    """事件快照是否可用：coverage 有该数据集且无错误码。无 coverage 条目时返回 default。"""
    entry = coverage.get(dataset) if isinstance(coverage, Mapping) else None
    if not isinstance(entry, Mapping):
        return default
    return not entry.get("errors")


def _checked_codes_from_audit(audit_row: Any) -> set[str]:
    if not isinstance(audit_row, dict):
        return set()
    status = str(audit_row.get("status") or "")
    if status not in {"success", "cached"}:
        return set()
    return {str(code) for code in (audit_row.get("checked_codes") or []) if code}


def _dragon_tiger_trade_date_covered(
    coverage: dict[str, Any],
    audit_row: Any,
    trade_date: str,
) -> bool:
    if bool(coverage.get("dragon_tiger_trade_date_covered")):
        return True
    dataset_coverage = coverage.get("dragon_tiger")
    if isinstance(dataset_coverage, dict):
        covered_trade_date = str(dataset_coverage.get("covered_trade_date") or "")
        if bool(dataset_coverage.get("trade_date_covered")) and covered_trade_date == trade_date:
            return True
        # 决策时刻可见性（d16f5a9）读的是前一交易日的榜单；视图声明它服务于本信号日即算覆盖，
        # 否则每只股票都会被打上 dragon_tiger 缺失、完整度/证据分各扣 10（L-170）。
        if bool(dataset_coverage.get("trade_date_covered")) and dataset_coverage.get("decision_visible_for") == trade_date:
            return True
    if not isinstance(audit_row, dict):
        return False
    status = str(audit_row.get("status") or "")
    covered_trade_date = str(audit_row.get("covered_trade_date") or "")
    return status in {"success", "cached"} and covered_trade_date == trade_date


def _tag_features(item: dict[str, Any]) -> None:
    """Annotate every feature with its provenance tag."""
    tags: dict[str, str] = {}
    data_quality = item.get("data_quality") or {}
    fallback_fields = set(data_quality.get("fallback_fields") or [])
    missing_fields = set(data_quality.get("missing_fields") or [])
    missing_fields.update(data_quality.get("optional_missing_fields") or [])
    for key in item:
        if key.startswith("_"):
            continue
        if key in fallback_fields:
            tags[key] = "FALLBACK"
        elif key in _OBSERVED_FIELDS:
            tags[key] = "OBSERVED"
        elif key in _DERIVED_SCORE_FIELDS:
            tags[key] = _derived_score_tag(data_quality)
        elif key in _OBSERVABLE_FIELDS:
            # 辅助字段：先看显式 fallback_fields，再看对应数据源类别是否缺失
            category = _FIELD_TO_MISSING_CATEGORY.get(key)
            if category and category in missing_fields:
                tags[key] = "FALLBACK"
            else:
                tags[key] = "OBSERVED"
        elif key in _SYNTHETIC_FIELDS:
            tags[key] = "ESTIMATED"
    item["_feature_tags"] = tags


def _derived_score_tag(data_quality: dict[str, Any]) -> str:
    if (
        bool(data_quality.get("strict_real_data"))
        and not data_quality.get("missing_fields")
        and not data_quality.get("fallback_fields")
    ):
        return "OBSERVED"
    return "DERIVED"


def _compute_authenticity(item: dict[str, Any]) -> float:
    """Compute authenticity with lower weight for derived and fallback features."""
    tags = item.get("_feature_tags", {})
    scored = [k for k in _SCORED_FEATURES if k in tags]
    if not scored:
        return 1.0
    weights = {
        "OBSERVED": 1.0,
        "DERIVED": 0.72,
        "ESTIMATED": 0.30,
        "FALLBACK": 0.12,
        "SYNTHETIC": 0.0,
    }
    total = sum(weights.get(tags[k], 0.0) for k in scored)
    return round(total / len(scored), 4)
