from __future__ import annotations

from typing import Any


SHORTLINE_FACTOR_SPECS: dict[str, dict[str, Any]] = {
    "limit_up_overheat": {
        "fields": ["limit_up_overheat_score", "limit_up_overheat_level"],
        "logic": "按市场涨停率、跌停压力、个股连板高度、涨幅位置和封板分歧计分，分数越高代表涨停情绪越过热。",
        "data_sources": ["Tushare/本地涨停池", "行情快照", "日 K 线派生字段"],
        "degradation": "缺少涨停明细时退化为市场涨停率、个股涨幅和位置估算；无法确认时给中性偏低风险。",
    },
    "sector_ladder": {
        "fields": ["sector_ladder_completeness_score", "sector_ladder_level"],
        "logic": "按同板块首板、二板、中高标、涨停数、强势股数量和题材热度衡量梯队完整度。",
        "data_sources": ["Tushare/本地涨停池", "本地概念板块池", "行情与行业字段"],
        "degradation": "缺少概念板块时退化为行业分组；缺少板块信息时输出 0 分和缺失标签。",
    },
    "capital_resonance": {
        "fields": ["capital_resonance_score", "capital_resonance_signal"],
        "logic": "按个股主力净流入、超大/大单方向、散户方向、板块资金排名和同板块资金扩散计分。",
        "data_sources": ["Tushare/本地资金流池", "行情成交额与量比回退"],
        "degradation": "缺少资金流时用涨幅、量比、成交额作可回测回退，并在解释中标明回退来源。",
    },
    "twenty_cm_risk": {
        "fields": ["is_20cm_stock", "twenty_cm_risk_score", "twenty_cm_risk_gate", "twenty_cm_risk_reason"],
        "logic": "识别创业板、科创板、北交所等 20cm 标的，并按涨幅、换手、流动性、位置和封板状态给风险闸门。",
        "data_sources": ["证券代码/交易所/市场板块", "行情快照", "日 K 线派生字段"],
        "degradation": "缺少市场板块时用代码前缀推断；无法识别则按非 20cm 处理但保留原因。",
    },
}


def apply_shared_shortline_factors(stocks: list[dict[str, Any]], snapshot: dict[str, Any]) -> None:
    """Compute reusable short-line factors once for every strategy plugin."""
    sector_stats = _sector_stats(stocks)
    sentiment_phase = _market_sentiment_phase(snapshot)
    main_industries = {str(v) for v in snapshot.get("main_industries", []) if str(v).strip()}

    for stock in stocks:
        sector = _sector_key(stock)
        stats = sector_stats.get(sector, {})
        is_main = bool(
            stock.get("industry") in main_industries
            or stock.get("concept_sector_name") in main_industries
        )
        stock["is_main_theme"] = is_main and stock.get("industry") != "未分类"
        stock["market_sentiment_phase"] = sentiment_phase

        _apply_sector_ladder_factor(stock, sector, stats)
        _apply_capital_resonance_factor(stock, stats)
        _apply_limit_up_overheat_factor(stock, snapshot)
        _apply_twenty_cm_risk_factor(stock)
        _apply_leader_factor(stock)
        _apply_dragon_tiger_factor(stock)
        _attach_factor_explanations(stock)


def _sector_stats(stocks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for stock in stocks:
        sector = _sector_key(stock)
        row = stats.setdefault(
            sector,
            {
                "stock_count": 0,
                "limit_up_count": 0,
                "first_board_count": 0,
                "second_board_count": 0,
                "high_board_count": 0,
                "height": 0,
                "strong_count": 0,
                "positive_flow_count": 0,
                "best_heat_rank": 999,
            },
        )
        row["stock_count"] += 1
        pct = _num(stock.get("pct"))
        boards = int(_num(stock.get("limit_up_consecutive_days"), 0))
        is_limit_up = bool(stock.get("is_limit_up")) or boards > 0
        if is_limit_up:
            row["limit_up_count"] += 1
            row["height"] = max(row["height"], max(1, boards))
            if boards <= 1:
                row["first_board_count"] += 1
            elif boards == 2:
                row["second_board_count"] += 1
            else:
                row["high_board_count"] += 1
        if pct >= 3:
            row["strong_count"] += 1
        if _num(stock.get("main_net_inflow_yi")) > 0:
            row["positive_flow_count"] += 1
        rank = int(_num(stock.get("concept_sector_heat_rank"), 999))
        row["best_heat_rank"] = min(row["best_heat_rank"], rank)
    return stats


def _apply_sector_ladder_factor(stock: dict[str, Any], sector: str, stats: dict[str, Any]) -> None:
    if not sector or sector == "未分类":
        stock["sector_ladder_completeness_score"] = 0.0
        stock["sector_ladder_level"] = "缺失"
        stock["sector_ladder_limit_up_count"] = 0
        stock["sector_ladder_height"] = 0
        return

    score = 0.0
    score += min(30.0, _num(stats.get("limit_up_count")) * 8)
    score += 15.0 if _num(stats.get("first_board_count")) > 0 else 0.0
    score += 20.0 if _num(stats.get("second_board_count")) > 0 else 0.0
    score += 20.0 if _num(stats.get("high_board_count")) > 0 else 0.0
    score += 10.0 if _num(stats.get("strong_count")) >= 3 else 0.0
    score += 8.0 if int(_num(stats.get("best_heat_rank"), 999)) <= 5 else 0.0
    score = round(min(100.0, score), 2)
    stock["sector_ladder_completeness_score"] = score
    stock["sector_ladder_level"] = _level(score, [(75, "完整"), (50, "展开"), (25, "初现")], "不完整")
    stock["sector_ladder_limit_up_count"] = int(_num(stats.get("limit_up_count")))
    stock["sector_ladder_height"] = int(_num(stats.get("height")))


def _apply_capital_resonance_factor(stock: dict[str, Any], stats: dict[str, Any]) -> None:
    data_quality = stock.get("data_quality") or {}
    missing = set(data_quality.get("missing_fields") or [])
    has_flow = "capital_flow" not in missing or data_quality.get("capital_flow_checked")

    if has_flow:
        main = _num(stock.get("main_net_inflow_yi"))
        ratio = _num(stock.get("main_net_inflow_ratio"))
        super_large = _num(stock.get("super_large_net_inflow_yi"))
        large = _num(stock.get("large_net_inflow_yi"))
        small = _num(stock.get("small_net_inflow_yi"))
        rank = int(_num(stock.get("sector_fund_rank"), 99))
        score = 45.0
        score += min(22.0, max(-18.0, main * 4))
        score += 10.0 if ratio >= 8 else 5.0 if ratio >= 3 else 0.0
        score += 14.0 if super_large > 0 and large >= 0 else 6.0 if super_large > 0 else 0.0
        score += 8.0 if small < 0 and main > 0 else 0.0
        score += 12.0 if rank <= 5 else 6.0 if rank <= 15 else -6.0
        score += 8.0 if _num(stats.get("positive_flow_count")) >= 2 else 0.0
        source = "capital_flow"
    else:
        pct = _num(stock.get("pct"))
        vol = _num(stock.get("vol5_20"), 1.0)
        amount = _num(stock.get("amount_yi"))
        score = 45.0
        score += 16.0 if pct >= 3 and vol >= 1.5 else 8.0 if pct > 1 and vol >= 1.2 else 0.0
        score += 10.0 if amount >= 5 else 4.0 if amount >= 3 else -6.0
        source = "price_volume_fallback"

    score = round(max(0.0, min(100.0, score)), 2)
    stock["capital_resonance_score"] = score
    stock["capital_resonance_signal"] = _level(
        score,
        [(75, "主力共振"), (60, "资金配合"), (45, "中性")],
        "资金背离",
    )
    stock["capital_resonance_source"] = source


def _apply_limit_up_overheat_factor(stock: dict[str, Any], snapshot: dict[str, Any]) -> None:
    sample = max(1, int(_num(snapshot.get("sample_count"), 1)))
    limit_up = _num(snapshot.get("limit_up_count"))
    limit_down = _num(snapshot.get("limit_down_count"))
    lu_rate = limit_up / sample
    boards = int(_num(stock.get("limit_up_consecutive_days"), 0))
    score = 0.0
    score += 28.0 if lu_rate >= 0.018 else 20.0 if lu_rate >= 0.012 else 10.0 if lu_rate >= 0.006 else 0.0
    score += 12.0 if limit_down >= max(5.0, limit_up) else 0.0
    score += 30.0 if boards >= 5 else 22.0 if boards >= 3 else 10.0 if boards >= 2 else 0.0
    score += 14.0 if bool(stock.get("is_limit_up")) or _num(stock.get("pct")) >= 8 else 0.0
    score += 12.0 if _num(stock.get("pos60")) > 95 and _num(stock.get("dist60")) > 30 else 0.0
    score += 8.0 if int(_num(stock.get("limit_up_open_times"))) >= 8 else 0.0
    score = round(min(100.0, score), 2)
    stock["limit_up_overheat_score"] = score
    stock["limit_up_overheat_level"] = _level(score, [(70, "高"), (45, "中"), (20, "低")], "正常")


def _apply_twenty_cm_risk_factor(stock: dict[str, Any]) -> None:
    is_20cm, basis = _is_twenty_cm_stock(stock)
    stock["is_20cm_stock"] = is_20cm
    if not is_20cm:
        stock["twenty_cm_risk_score"] = 0.0
        stock["twenty_cm_risk_gate"] = "not_applicable"
        stock["twenty_cm_risk_reason"] = basis
        return

    score = 15.0
    reasons: list[str] = [basis]
    if _num(stock.get("pct")) >= 12:
        score += 25.0
        reasons.append("当日涨幅进入 20cm 高波动区")
    if bool(stock.get("is_limit_up")):
        score += 20.0
        reasons.append("20cm 涨停后追高成交约束更强")
    if _num(stock.get("turnover_rate")) >= 18:
        score += 18.0
        reasons.append("换手过高")
    if _num(stock.get("amount_yi")) < 3:
        score += 12.0
        reasons.append("成交额不足")
    if _num(stock.get("pos60")) > 95 and _num(stock.get("dist60")) > 30:
        score += 15.0
        reasons.append("中期位置过高")
    score = round(min(100.0, score), 2)
    stock["twenty_cm_risk_score"] = score
    stock["twenty_cm_risk_gate"] = "block" if score >= 70 else "caution" if score >= 35 else "pass"
    stock["twenty_cm_risk_reason"] = "；".join(reasons)


def _apply_leader_factor(stock: dict[str, Any]) -> None:
    # 龙头候选评分：度量"龙头属性"（主线/板块阶梯/资金共振/涨停/突破），用于选股逻辑，非胜率预测。
    # 权重设计基于因果（主力资金行为），非 point-biserial 优化。
    # 诊断（第99轮 T3，148 笔去重样本）：
    #   - shortline_leader_score 对胜率 point-biserial=0.046（无预测力）
    #   - capital_resonance/sector_ladder 占 52% 权重但分桶胜率无单调关系（CR: 0→63.6%/1-50→70.5%/>=50→66.7%）
    #   - 阈值 68 未作为硬门槛（133/148 笔 leader_score<60 仍被推荐，胜率 69.9%）
    # 结论：权重调整对胜率影响有限，胜率主要由 trailing stop + 策略 universe 决定。
    # 调整权重前必须回测验证（见 alpha-decay-false-positive 教训），避免破坏选股逻辑。
    score = 0.0
    # is_main_theme 占 24 分（最大权重），但 95.7% 的推荐都是 True（第105轮 T1 衰减复检）。
    # 衰减 38.8%（point-biserial 0.125→-0.077），但近期 False 样本仅 5 笔（不可信）。
    # True 胜率前期 72.1%→近期 61.6%（降 10.5%，市场环境变化），False 样本太少无法判定。
    # 95.7% 都是 True 说明该因子几乎无区分度（只排除 4.3%），24 分等于普遍提分。
    # 未来优化方向：降低 is_main_theme 权重，把分数让给有区分度的因子（需回测验证）。
    score += 24.0 if stock.get("is_main_theme") else 0.0
    score += min(28.0, _num(stock.get("sector_ladder_completeness_score")) * 0.28)
    score += min(24.0, _num(stock.get("capital_resonance_score")) * 0.24)
    score += 16.0 if bool(stock.get("is_limit_up")) or _num(stock.get("pct")) >= 5 else 0.0
    score += 8.0 if bool(stock.get("breakout20")) else 0.0
    # breakout60 加入评分（T3 第82轮）：近期预测力增强（+0.023→+0.190），
    # 6-7月 breakout60=True WR=85.7% avg+5.20%（vs False 54.3%）
    # 反事实验证：加 8 分新增 41 笔推荐 WR=58.5% 净=+46.57%（0 移除，纯增量）
    score += 8.0 if bool(stock.get("breakout60")) else 0.0
    # 中期强势因子（T1 第83轮）：dist60>=9.4 表示价格站上60日线 9.4%+，中期趋势确立
    # 数据验证（去重 282 笔）：dist60>=9.4 WR=66.0% avg+2.28%（vs <9.4 的 44.0%/+0.36%）
    # 高潮阶段尤佳：dist60>=9.4 WR=75.0% avg+4.26%（vs <9.4 的 40.7%）
    # 近期增强：5月 r=-0.106 → 6-7月 r=+0.347
    # 反事实验证（扣费0.10%/笔）：加 6 分新增 53 笔 WR=66.0% 净=+115.72%（0 移除）
    # 正交性：与 breakout60 重叠仅 6/58，与 leader 现有项相关 |r|<0.32（独立 alpha）
    # 因果：60日线是机构中期成本线，站上方 9%+ = 中期趋势确立且资金浮盈，主力不轻易放弃
    score += 6.0 if _num(stock.get("dist60")) >= 9.4 else 0.0
    score = round(min(100.0, score), 2)
    stock["shortline_leader_score"] = score
    stock["is_shortline_leader_candidate"] = score >= 68


def _apply_dragon_tiger_factor(stock: dict[str, Any]) -> None:
    seats = int(_num(stock.get("dragon_tiger_buy_seat_count"), 0))
    net_amt = _num(stock.get("dragon_tiger_net_amount_yi"))
    inst_net = _num(stock.get("dragon_tiger_institution_net_buy_yi"))
    if not stock.get("dragon_tiger_is_on_list"):
        stock["dragon_tiger_buy_dispersion"] = "未上榜"
        stock["dragon_tiger_inst_buy_ratio"] = 0.0
        return
    if seats and seats <= 2:
        dispersion = "集中"
    elif seats and seats <= 5:
        dispersion = "一般"
    else:
        dispersion = "分散"
    stock["dragon_tiger_buy_dispersion"] = dispersion
    stock["dragon_tiger_inst_buy_ratio"] = round(inst_net / abs(net_amt), 2) if net_amt else 0.0


def _attach_factor_explanations(stock: dict[str, Any]) -> None:
    stock["shortline_factor_explanations"] = {
        "limit_up_overheat": {
            **SHORTLINE_FACTOR_SPECS["limit_up_overheat"],
            "value": stock.get("limit_up_overheat_level"),
            "score": stock.get("limit_up_overheat_score"),
        },
        "sector_ladder": {
            **SHORTLINE_FACTOR_SPECS["sector_ladder"],
            "value": stock.get("sector_ladder_level"),
            "score": stock.get("sector_ladder_completeness_score"),
        },
        "capital_resonance": {
            **SHORTLINE_FACTOR_SPECS["capital_resonance"],
            "value": stock.get("capital_resonance_signal"),
            "score": stock.get("capital_resonance_score"),
            "source": stock.get("capital_resonance_source"),
        },
        "twenty_cm_risk": {
            **SHORTLINE_FACTOR_SPECS["twenty_cm_risk"],
            "value": stock.get("twenty_cm_risk_gate"),
            "score": stock.get("twenty_cm_risk_score"),
            "reason": stock.get("twenty_cm_risk_reason"),
        },
    }


def _sector_key(stock: dict[str, Any]) -> str:
    return str(stock.get("concept_sector_name") or stock.get("industry") or "未分类")


def _market_sentiment_phase(snapshot: dict[str, Any]) -> str:
    sample = max(1, int(_num(snapshot.get("sample_count"), 1)))
    limit_up = _num(snapshot.get("limit_up_count"))
    limit_down = _num(snapshot.get("limit_down_count"))
    lu_rate = limit_up / sample
    ld_rate = limit_down / sample
    if lu_rate > 0.012 and (ld_rate == 0 or lu_rate / max(ld_rate, 0.0001) > 3):
        return "高潮"
    if lu_rate > 0.005:
        return "活跃"
    if lu_rate >= 0.001:
        return "平淡"
    return "冰点"


def _is_twenty_cm_stock(stock: dict[str, Any]) -> tuple[bool, str]:
    code = str(stock.get("code") or "")
    exchange = str(stock.get("exchange") or "").upper()
    market = str(stock.get("market") or stock.get("board") or "")
    if "创业" in market or code.startswith(("300", "301")):
        return True, "创业板 20cm 标的"
    if "科创" in market or code.startswith(("688", "689")):
        return True, "科创板 20cm 标的"
    if "北交" in market or exchange == "BJ" or code.startswith(("8", "4")):
        return True, "北交所高波动标的"
    if not exchange and not market:
        return False, "缺少板块信息，按代码前缀未识别为 20cm"
    return False, "非 20cm 标的"


def _level(score: float, thresholds: list[tuple[float, str]], default: str) -> str:
    for threshold, label in thresholds:
        if score >= threshold:
            return label
    return default


def _num(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
