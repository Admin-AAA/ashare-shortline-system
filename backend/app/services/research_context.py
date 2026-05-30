from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# 股票名称内存缓存：避免在 build_research_context 中重复查询 stocks 表
_stock_name_cache: dict[str, str] = {}


CONTEXT_SCHEMA_VERSION = "research_context.v1"

QUOTE_FIELDS = {
    "price",
    "pct",
    "amount_yi",
    "turnover_rate",
    "free_mv_yi",
    "total_mv_yi",
    "pe_ttm",
    "main_net_inflow_yi",
}

KLINE_DERIVED_FIELDS = {
    "chg20",
    "chg60",
    "dist20",
    "dist60",
    "pos20",
    "pos60",
    "pos120",
    "eff20",
    "vol5_20",
    "upper_shadow_count20",
    "max_dd20",
    "max_dd60",
    "breakout20",
    "breakout60",
    "off_high_pct",
    "off_high60_pct",
    "limit_up_days20",
    "ma5",
    "ma10",
    "ma20",
    "ma60",
    "bias_ma5",
    "bias_ma10",
    "bias_ma20",
    "support_ma5",
    "support_ma10",
    "volume_ratio_5d",
    "volume_ratio_vs_5d",
    "volume_ratio_vs_20d",
    "volume_breakout_signal",
    "volume_shrink_pullback",
    "macd_status",
    "macd_dif",
    "macd_dea",
    "macd_bar",
    "macd_signal",
    "rsi_6",
    "rsi_12",
    "rsi_24",
    "rsi_status",
    "rsi_signal",
    "dsa_trend_score",
    "dsa_bias_score",
    "dsa_volume_score",
    "dsa_support_score",
    "dsa_macd_score",
    "dsa_rsi_score",
    "dsa_signal_score",
}

IMPUTED_WHEN_MISSING = {
    "main_net_inflow": {
        "main_net_inflow_yi",
        "inflow_5d_yi",
        "inflow_10d_yi",
        "capital_flow_score",
        "sector_fund_rank",
    },
    "valuation": {
        "pe_ttm",
        "profit_ratio",
        "avg_cost",
        "cost_90_low",
        "cost_90_high",
        "concentration_90",
        "chip_concentration",
        "chip_health",
        "chip_health_score",
        "fundamental_score",
    },
    "news": {
        "latest_news",
        "risk_alerts",
        "positive_catalysts",
        "news_risk_count",
        "positive_catalyst_count",
        "sentiment_summary",
        "intelligence_score",
    },
    "announcements": {
        "announcement_negative_count",
    },
    "dragon_tiger": {
        "dragon_tiger_is_on_list",
        "dragon_tiger_recent_count",
        "dragon_tiger_latest_date",
    },
    "theme_tags": {
        "theme_tags",
    },
}

QUALITY_CRITICAL_MISSING = {"news", "announcements", "theme_tags", "capital_flow", "dragon_tiger"}

PRIVATE_KEYS_TO_DROP = {"_bars", "dsa_profile"}


@dataclass(frozen=True)
class ResearchContext:
    security: dict[str, Any]
    quote: dict[str, Any]
    kline: dict[str, Any]
    features: dict[str, Any]
    themes: dict[str, Any]
    events: dict[str, Any]
    market: dict[str, Any]
    quality: dict[str, Any]
    source_trace: dict[str, Any]
    legacy_stock: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CONTEXT_SCHEMA_VERSION,
            "security": self.security,
            "quote": self.quote,
            "kline": self.kline,
            "features": self.features,
            "themes": self.themes,
            "events": self.events,
            "market": self.market,
            "quality": self.quality,
            "source_trace": self.source_trace,
        }

    def to_skill_input(self) -> dict[str, Any]:
        return dict(self.legacy_stock)

    def to_feature_snapshot(self) -> dict[str, Any]:
        snapshot = dict(self.legacy_stock)
        snapshot["research_context"] = self.to_dict()
        return snapshot


def build_research_contexts(stocks: list[dict[str, Any]], snapshot: dict[str, Any]) -> list[ResearchContext]:
    return [build_research_context(stock, snapshot) for stock in stocks]


def _resolve_stock_name_from_db(code: str) -> str | None:
    """从 stocks 表查询股票名称，若查询不到则返回 None。

    使用模块级缓存避免对同一 code 重复查询。
    """
    if code in _stock_name_cache:
        return _stock_name_cache[code] if _stock_name_cache[code] else None
    try:
        from app.db.session import SessionLocal
        from app.models.tables import Stock
    except ImportError:
        logger.debug("无法导入 DB 模块，跳过 stocks 表名称查询")
        return None
    session = SessionLocal()
    try:
        stock_row = session.query(Stock).filter(Stock.code == code).first()
        if stock_row and stock_row.name:
            name = str(stock_row.name).strip()
            _stock_name_cache[code] = name
            return name
        else:
            _stock_name_cache[code] = ""
            return None
    except Exception:
        logger.debug("查询 stocks 表名称失败 code=%s", code, exc_info=True)
        _stock_name_cache[code] = ""
        return None
    finally:
        session.close()


def build_research_context(stock: dict[str, Any], snapshot: dict[str, Any]) -> ResearchContext:
    data_quality = dict(stock.get("data_quality") or {})
    source_trace = _source_trace(stock, snapshot)
    missing_fields = _expanded_missing_fields(stock, data_quality)
    field_quality = _field_quality(stock, source_trace, missing_fields)
    quality = {
        **data_quality,
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "strict_real_data": bool(data_quality.get("strict_real_data", stock.get("data_source") == "real_market")),
        "missing_fields": missing_fields,
        "field_quality": field_quality,
        "fallback_fields": sorted(set(data_quality.get("fallback_fields") or [])),
        "synthetic_fields": sorted(
            field for field, row in field_quality.items() if row.get("status") in {"estimated", "fallback"}
        ),
        "data_completeness_score": _data_completeness_score(missing_fields),
        "evidence_quality_score": _pool_adjusted_evidence_score(
            _evidence_quality_score(field_quality, missing_fields),
            data_quality,
        ),
    }
    # ── 股票名称补全：若 name 缺失或 fallback 回了 code 本身，尝试从 stocks 表查询 ──
    stock_code = str(stock.get("code") or "")
    stock_name = str(stock.get("name") or "").strip()
    if not stock_name or stock_name == stock_code:
        resolved = _resolve_stock_name_from_db(stock_code)
        if resolved:
            stock_name = resolved
    security = {
        "code": stock_code,
        "name": stock_name or stock_code,
        "exchange": stock.get("exchange"),
        "is_st": bool(stock.get("is_st", False)),
        "list_status": stock.get("list_status", "listed"),
    }
    quote = {
        "price": stock.get("price"),
        "pct": stock.get("pct"),
        "amount_yi": stock.get("amount_yi"),
        "turnover_rate": stock.get("turnover_rate"),
        "free_mv_yi": stock.get("free_mv_yi"),
        "total_mv_yi": stock.get("total_mv_yi"),
        "pe_ttm": stock.get("pe_ttm"),
        "main_net_inflow_yi": stock.get("main_net_inflow_yi"),
        "source": source_trace.get("quote_source"),
        "timestamp": stock.get("quote_time"),
    }
    kline = {
        "latest_trade_date": stock.get("latest_trade_date"),
        "bars_count": quality.get("kline_days"),
        "source": source_trace.get("kline_source"),
        "summary": {key: stock.get(key) for key in sorted(KLINE_DERIVED_FIELDS) if key in stock},
    }
    themes = {
        "industry": stock.get("industry") or "unknown",
        "is_main_theme": bool(stock.get("is_main_theme")),
        "main_industries": snapshot.get("main_industries", []),
        "theme_tags": list(stock.get("theme_tags") or []),
    }
    # ── 数据状态展示：区分「已检查但未上榜/无数据」vs「未检查」，供推荐解释渲染 ──
    _dt_check_status: str
    if stock.get("dragon_tiger_is_on_list"):
        _dt_check_status = "on_list"
    elif data_quality.get("dragon_tiger_checked_empty"):
        _dt_check_status = "checked_empty"
    else:
        _dt_check_status = "unchecked"

    _news_check_status: str
    if stock.get("news_risk_count", 0) > 0 or stock.get("positive_catalyst_count", 0) > 0 or (
        stock.get("latest_news") and not _looks_unavailable(stock.get("latest_news"))
    ):
        _news_check_status = "has_data"
    elif data_quality.get("news_checked_empty"):
        _news_check_status = "checked_empty"
    elif data_quality.get("news_checked"):
        _news_check_status = "has_data"
    else:
        _news_check_status = "unchecked"

    _announcements_check_status: str
    if data_quality.get("announcements_checked"):
        _announcements_check_status = "has_data" if (stock.get("announcement_negative_count") or 0) > 0 else "checked_empty"
    elif data_quality.get("announcements_checked_empty"):
        _announcements_check_status = "checked_empty"
    else:
        _announcements_check_status = "unchecked"

    _capital_flow_check_status: str
    if data_quality.get("capital_flow_checked"):
        _capital_flow_check_status = "has_data"
    elif data_quality.get("capital_flow_checked_empty"):
        _capital_flow_check_status = "checked_empty"
    else:
        _capital_flow_check_status = "unchecked"

    events = {
        "limit_up_days20": stock.get("limit_up_days20", 0),
        "is_limit_up": stock.get("is_limit_up", False),
        "limit_up_consecutive_days": stock.get("limit_up_consecutive_days", 0),
        "limit_up_first_time": stock.get("limit_up_first_time"),
        "limit_up_last_time": stock.get("limit_up_last_time"),
        "limit_up_open_times": stock.get("limit_up_open_times"),
        "limit_up_fd_amount_yi": stock.get("limit_up_fd_amount_yi"),
        "limit_up_turnover_ratio": stock.get("limit_up_turnover_ratio"),
        "announcement_negative_count": stock.get("announcement_negative_count", 0),
        "news_risk_count": stock.get("news_risk_count", 0),
        "dragon_tiger_is_on_list": stock.get("dragon_tiger_is_on_list", False),
        "dragon_tiger_recent_count": stock.get("dragon_tiger_recent_count", 0),
        "latest_news": stock.get("latest_news"),
        # 数据检查状态：供推荐解释区分「已检查但无数据」与「未检查」
        "dragon_tiger_check_status": _dt_check_status,
        "news_check_status": _news_check_status,
        "announcements_check_status": _announcements_check_status,
        "capital_flow_check_status": _capital_flow_check_status,
    }
    market = {
        "snapshot_id": snapshot.get("snapshot_id"),
        "trade_date": snapshot.get("trade_date"),
        "sample_count": snapshot.get("sample_count", 0),
        "regime": (snapshot.get("breadth") or {}).get("regime"),
        "risk": (snapshot.get("breadth") or {}).get("risk"),
        "main_industries": snapshot.get("main_industries", []),
    }
    features = _context_features(stock)
    legacy_stock = _legacy_skill_stock(stock, quality, source_trace)
    return ResearchContext(
        security=security,
        quote=quote,
        kline=kline,
        features=features,
        themes=themes,
        events=events,
        market=market,
        quality=quality,
        source_trace=source_trace,
        legacy_stock=legacy_stock,
    )


def _legacy_skill_stock(
    stock: dict[str, Any],
    quality: dict[str, Any],
    source_trace: dict[str, Any],
) -> dict[str, Any]:
    legacy = {
        key: value
        for key, value in stock.items()
        if key not in PRIVATE_KEYS_TO_DROP and not key.startswith("_")
    }
    legacy["data_quality"] = quality
    legacy["source_trace"] = source_trace
    legacy["research_context_version"] = CONTEXT_SCHEMA_VERSION
    legacy["field_quality"] = quality.get("field_quality", {})
    legacy["data_completeness_score"] = quality.get("data_completeness_score", 0.0)
    legacy["evidence_quality_score"] = quality.get("evidence_quality_score", 0.0)
    legacy["market_sample_count"] = stock.get("market_sample_count", 0)
    return legacy


def _context_features(stock: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in stock.items()
        if key not in PRIVATE_KEYS_TO_DROP
        and not key.startswith("_")
        and key
        not in {
            "code",
            "name",
            "exchange",
            "industry",
            "is_st",
            "quote_source",
            "kline_source",
            "quote_time",
            "latest_trade_date",
            "data_quality",
        }
    }


def _source_trace(stock: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "data_source": stock.get("data_source", "unknown"),
        "quote_source": stock.get("quote_source") or (stock.get("data_quality") or {}).get("quote_source"),
        "quote_raw_sources": (stock.get("data_quality") or {}).get("quote_raw_sources", []),
        "quote_source_count": (stock.get("data_quality") or {}).get("quote_source_count", 0),
        "quote_quality_score": (stock.get("data_quality") or {}).get("quote_quality_score", 0),
        "quote_conflicts": (stock.get("data_quality") or {}).get("quote_conflicts", []),
        "quote_cache_status": (stock.get("data_quality") or {}).get("quote_cache_status"),
        "kline_source": stock.get("kline_source") or (stock.get("data_quality") or {}).get("bars_source"),
        "kline_cache_status": (stock.get("data_quality") or {}).get("kline_cache_status"),
        "latest_trade_date": stock.get("latest_trade_date"),
        "quote_time": stock.get("quote_time"),
        "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_source": (snapshot.get("breadth") or {}).get("data_source"),
        "market_generated_at": (snapshot.get("breadth") or {}).get("generated_at"),
    }


def _expanded_missing_fields(stock: dict[str, Any], data_quality: dict[str, Any]) -> list[str]:
    missing = set(data_quality.get("missing_fields") or [])
    if data_quality.get("news_checked_empty"):
        missing.discard("news")
    if data_quality.get("announcements_checked_empty"):
        missing.discard("announcements")
    if data_quality.get("dragon_tiger_checked_empty"):
        missing.discard("dragon_tiger")
    if data_quality.get("capital_flow_checked"):
        missing.discard("capital_flow")
        missing.discard("main_net_inflow")
    if data_quality.get("capital_flow_checked_empty"):
        missing.discard("capital_flow")
        missing.discard("main_net_inflow")
    if data_quality.get("theme_tags_fallback_from_industry"):
        missing.discard("theme_tags")
    if not stock.get("industry") or stock.get("industry") == "未分类":
        missing.add("industry")
    if _looks_unavailable(stock.get("latest_news")) and not data_quality.get("news_checked_empty"):
        missing.add("news")
    if (
        not stock.get("risk_alerts")
        and int(float(stock.get("announcement_negative_count") or 0)) == 0
        and not data_quality.get("announcements_checked")
        and not data_quality.get("announcements_checked_empty")
    ):
        missing.add("announcements")
    if (
        not stock.get("dragon_tiger_latest_date")
        and int(float(stock.get("dragon_tiger_recent_count") or 0)) == 0
        and not data_quality.get("dragon_tiger_checked_empty")
    ):
        missing.add("dragon_tiger")
    if (
        float(stock.get("main_net_inflow_yi") or 0) == 0
        and "main_net_inflow_yi" not in (stock.get("_feature_tags") or {})
        and not data_quality.get("capital_flow_checked")
        and not data_quality.get("capital_flow_checked_empty")
    ):
        missing.add("capital_flow")
    if not stock.get("theme_tags") and not data_quality.get("theme_tags_fallback_from_industry"):
        missing.add("theme_tags")
    return sorted(missing)


def _field_quality(
    stock: dict[str, Any],
    source_trace: dict[str, Any],
    missing_fields: list[str],
) -> dict[str, dict[str, Any]]:
    missing = set(missing_fields)
    feature_tags = stock.get("_feature_tags") or {}
    quality: dict[str, dict[str, Any]] = {}
    for field in QUOTE_FIELDS:
        tag = str(feature_tags.get(field) or "").upper()
        if tag == "FALLBACK":
            quality[field] = _quality_row("fallback", source_trace.get("quote_source"), 0.15)
        else:
            quality[field] = _quality_row(
                status="missing" if field in _fields_for_missing(missing) or _is_missing_value(stock.get(field)) else "observed",
                source=source_trace.get("quote_source"),
                confidence=0.0 if _is_missing_value(stock.get(field)) else 0.9,
            )
    for field in KLINE_DERIVED_FIELDS:
        if field in stock:
            quality[field] = _quality_row("derived", source_trace.get("kline_source"), 0.78)
    for field in _fields_for_missing(missing):
        if field in stock:
            quality[field] = _quality_row("fallback", source_trace.get("data_source"), 0.18)
    for field, tag in feature_tags.items():
        if field in quality:
            continue
        upper_tag = str(tag).upper()
        if upper_tag == "DERIVED":
            quality[field] = _quality_row("derived", source_trace.get("kline_source"), 0.72)
        elif upper_tag in {"ESTIMATED", "SYNTHETIC"}:
            quality[field] = _quality_row("estimated", source_trace.get("data_source"), 0.28)
        elif upper_tag == "FALLBACK":
            quality[field] = _quality_row("fallback", source_trace.get("data_source"), 0.12)
        elif upper_tag == "OBSERVED":
            quality[field] = _quality_row("observed", source_trace.get("quote_source"), 0.9)
    if "industry" in stock:
        quality["industry"] = _quality_row(
            "missing" if "industry" in missing else "observed",
            source_trace.get("quote_source"),
            0.0 if "industry" in missing else 0.7,
        )
    return quality


def _fields_for_missing(missing_fields: set[str]) -> set[str]:
    fields: set[str] = set()
    for gap in missing_fields:
        fields.update(IMPUTED_WHEN_MISSING.get(gap, set()))
    return fields


def _quality_row(status: str, source: Any, confidence: float) -> dict[str, Any]:
    return {
        "status": status,
        "source": source or "unknown",
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
    }


def _data_completeness_score(missing_fields: list[str]) -> float:
    penalty = 0.0
    for field in missing_fields:
        penalty += 10.0 if field in QUALITY_CRITICAL_MISSING else 4.0
    return round(max(25.0, 100.0 - penalty), 2)


def _evidence_quality_score(field_quality: dict[str, dict[str, Any]], missing_fields: list[str]) -> float:
    if not field_quality:
        return _data_completeness_score(missing_fields)
    confidence = sum(float(row.get("confidence") or 0.0) for row in field_quality.values()) / len(field_quality)
    status_penalty = 0.0
    for row in field_quality.values():
        status = str(row.get("status") or "")
        if status == "fallback":
            status_penalty += 0.01
        elif status == "estimated":
            status_penalty += 0.008
    completeness = _data_completeness_score(missing_fields) / 100
    score = (confidence * 0.68 + completeness * 0.32) * 100 - status_penalty * 100
    return round(max(0.0, min(100.0, score)), 2)


def _pool_adjusted_evidence_score(base_score: float, data_quality: dict[str, Any]) -> float:
    score = float(base_score)
    quote_sources = int(data_quality.get("quote_source_count") or 0)
    quote_quality = float(data_quality.get("quote_quality_score") or 0)
    conflicts = data_quality.get("quote_conflicts") or []
    if quote_sources >= 3:
        score += 6
    elif quote_sources >= 2:
        score += 3
    elif quote_sources == 1:
        score -= 4
    if quote_quality:
        score = score * 0.85 + quote_quality * 0.15
    if conflicts:
        score -= min(18, len(conflicts) * 6)
    if data_quality.get("quote_cache_status") == "stale_cache":
        score -= 8
    missing_fields = set(data_quality.get("missing_fields") or [])
    if {"news", "announcements"} & missing_fields:
        score -= 3
    if "dragon_tiger" in missing_fields:
        score -= 1.5
    if "capital_flow" in missing_fields:
        score -= 2
    return round(max(0.0, min(100.0, score)), 2)


def _looks_unavailable(value: Any) -> bool:
    text = str(value or "")
    return not text.strip() or "未接入" in text or "暂无" in text


def _is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in {"", "-", "None", "nan"}
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False
