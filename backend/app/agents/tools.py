"""
Agno Agent 工具函数。

用 Agno @tool 装饰器包装本地数据查询工具。所有 tool 函数只接受简单类型
参数（str, int），通过 session_id 从 AgentSessionRegistry 获取 db session，
返回 JSON-serializable 的 dict。

设计约束:
  - 不向 LLM 暴露 db Session 或 ORM 对象
  - 所有异常 catch 后返回 error dict，不 raise
  - 只做数据查询和简单统计，不做复杂特征计算
"""

from typing import Any

from sqlalchemy import desc, func, select

from app.agents.session_registry import AgentSessionRegistry
from app.models.tables import DailyBar, DailyIndicator, UnifiedQuote


# ── Agno tool 装饰器 ──────────────────────────────────────────────────

try:
    from agno.tools import tool
except ImportError:
    # 开发期兼容：agno 未安装时提供一个 no-op 装饰器
    def tool(func):  # type: ignore[no-redef]
        return func


# ── 内部辅助函数 ──────────────────────────────────────────────────────

def _num(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _bias(price: float, ma: float) -> float:
    return (price - ma) / ma * 100 if ma else 0.0


def _ma(values: list[float], window: int) -> float:
    if len(values) < window:
        return values[-1] if values else 0.0
    return sum(values[-window:]) / window


# ── Tool 函数 ─────────────────────────────────────────────────────────


@tool
def query_market_snapshot(session_id: str) -> dict:
    """查询市场全景快照。

    从 Agent 会话的 snapshot 中获取当前交易日的市场概况，
    包括涨跌家数、涨停跌停数、主线行业、市场宽度等。

    Args:
        session_id: Agent 会话 ID
    """
    try:
        ctx = AgentSessionRegistry.get(session_id)
        if ctx is None:
            return {"error": "会话不存在或已过期", "session_id": session_id}

        snapshot = ctx.snapshot
        breadth = snapshot.get("breadth", {})
        return {
            "trade_date": snapshot.get("trade_date"),
            "sample_count": int(snapshot.get("sample_count") or 0),
            "up_count": int(snapshot.get("up_count") or 0),
            "down_count": int(snapshot.get("down_count") or 0),
            "limit_up_count": int(snapshot.get("limit_up_count") or 0),
            "limit_down_count": int(snapshot.get("limit_down_count") or 0),
            "main_industries": snapshot.get("main_industries") or [],
            "breadth": {
                "regime": breadth.get("regime"),
                "risk": breadth.get("risk"),
                "hot_theme": breadth.get("hot_theme"),
            },
        }
    except Exception as e:
        return {"error": f"查询市场快照失败: {e}"}


@tool
def query_stock_detail(code: str, session_id: str) -> dict:
    """查询个股全貌。

    从 stocks_cache 获取已构建的股票详情；若未命中则从本地数据库
    （DailyBar + DailyIndicator + UnifiedQuote）查询并构建。

    返回价格、技术指标、量能、风险、资金等结构化字段。

    Args:
        code: 股票代码（如 600519）
        session_id: Agent 会话 ID
    """
    try:
        ctx = AgentSessionRegistry.get(session_id)
        if ctx is None:
            return {"error": "会话不存在或已过期", "session_id": session_id}

        # 缓存命中
        if code in ctx.stocks_cache:
            cached = dict(ctx.stocks_cache[code])
            # 去掉不可序列化的内部字段
            cached.pop("_bars", None)
            cached.pop("dsa_profile", None)
            return cached

        db = ctx.db

        # 查询最新一条 UnifiedQuote
        quote_row = db.scalar(
            select(UnifiedQuote)
            .where(UnifiedQuote.code == code)
            .order_by(UnifiedQuote.trade_date.desc())
            .limit(1)
        )
        if quote_row is None:
            return {"error": "未找到该股票数据", "code": code}

        import json as _json

        quote_data = _json.loads(quote_row.quote_json) if quote_row.quote_json else {}

        # 查询最近 60 条日 K
        bars = (
            db.scalars(
                select(DailyBar)
                .where(DailyBar.code == code)
                .order_by(DailyBar.trade_date.desc())
                .limit(60)
            )
            .all()
        )
        if not bars:
            return {"error": "未找到该股票的日K数据", "code": code}

        # 按日期升序排列
        bars = list(reversed(bars))

        # 查询最新 DailyIndicator
        indicator_row = db.scalar(
            select(DailyIndicator)
            .where(DailyIndicator.code == code)
            .order_by(DailyIndicator.trade_date.desc())
            .limit(1)
        )
        indicator = indicator_row

        latest = bars[-1]
        closes = [float(bar.close) for bar in bars]
        highs = [float(bar.high) for bar in bars]
        lows = [float(bar.low) for bar in bars]
        volumes = [float(bar.volume) for bar in bars]

        price = _num(quote_data.get("price"), float(latest.close))
        pct = _num(quote_data.get("pct"), float(latest.pct_chg))
        turnover_rate = _num(quote_data.get("turnover_rate"), float(latest.turnover_rate))
        amount_yi = _num(quote_data.get("amount_yi"), float(latest.amount or 0) / 100000000)
        free_mv_yi = _num(quote_data.get("free_mv_yi"))

        # 均线
        _ma5 = float(latest.ma5) if latest.ma5 is not None else _ma(closes, 5)
        _ma10 = float(latest.ma10) if latest.ma10 is not None else _ma(closes, 10)
        _ma20 = float(latest.ma20) if latest.ma20 is not None else _ma(closes, 20)
        _ma60 = float(latest.ma60) if latest.ma60 is not None else _ma(closes, 60)

        dist20 = _bias(price, _ma20)
        dist60 = _bias(price, _ma60)

        # 量能
        ma_vol20 = _ma(volumes, 20)
        vol5_20 = float(latest.volume_ratio_5d) if latest.volume_ratio_5d is not None else (
            _ma(volumes, 5) / ma_vol20 if ma_vol20 else 1.0
        )

        # primary_excess20 用 chg20 近似：相对于市场均值的超额
        chg20 = ((closes[-1] - closes[-21]) / closes[-21] * 100) if len(closes) >= 21 else 0.0

        # 最大回撤和上影线
        max_high20 = max(highs[-20:]) if len(highs) >= 20 else max(highs)
        off_high_pct = (price - max_high20) / max_high20 * 100 if max_high20 else 0.0

        max_dd20: float = 0.0
        if indicator is not None and indicator.max_dd20 is not None:
            max_dd20 = float(indicator.max_dd20)
        else:
            peak = closes[-min(20, len(closes))]
            for i in range(len(closes) - min(20, len(closes)) + 1, len(closes)):
                peak = max(peak, closes[i])
                dd = (closes[i] - peak) / peak * 100 if peak else 0
                max_dd20 = min(max_dd20, dd)

        upper_shadow_count20: int = 0
        if indicator is not None:
            upper_shadow_count20 = int(indicator.upper_shadow_count20 or 0)
        else:
            upper_shadow_count20 = _upper_shadow_count(bars[-20:])

        limit_up_days20 = sum(1 for bar in bars[-20:] if float(bar.pct_chg or 0) >= 9.8)

        # 资金
        main_net_flow = quote_data.get("main_net_inflow_yi")

        # 数据质量
        missing_fields = _detect_missing(quote_data, bars)
        data_quality = {
            "missing_fields": missing_fields,
            "kline_days": len(bars),
            "quote_source_count": int(quote_row.source_count or 0),
            "quote_quality_score": float(quote_row.quality_score or 0),
        }

        result = {
            "code": code,
            "name": quote_data.get("name") or code,
            "industry": quote_data.get("industry") or "未分类",
            "price": round(price, 3),
            "pct": round(pct, 3),
            "turnover_rate": round(turnover_rate, 3),
            "amount_yi": round(amount_yi, 3),
            "free_mv_yi": round(free_mv_yi, 3),
            "ma5": round(_ma5, 3),
            "ma10": round(_ma10, 3),
            "ma20": round(_ma20, 3),
            "ma60": round(_ma60, 3),
            "dist20": round(dist20, 3),
            "dist60": round(dist60, 3),
            "off_high_pct": round(off_high_pct, 3),
            "vol5_20": round(vol5_20, 3),
            "primary_excess20": round(chg20, 3),
            "max_dd20": round(max_dd20, 3),
            "upper_shadow_count20": upper_shadow_count20,
            "limit_up_days20": limit_up_days20,
            "data_quality": data_quality,
        }
        if main_net_flow is not None:
            result["main_net_flow"] = round(_num(main_net_flow), 3)

        return result
    except Exception as e:
        return {"error": f"查询股票详情失败: {e}", "code": code}


@tool
def query_skill_signals(code: str, skill_id: str, session_id: str) -> dict:
    """查询某个 Skill 对某只股票的策略规则评分。

    从 strategy_signals_cache 获取该 Skill 对指定股票的评分结果。
    若该 Skill 没有 strategy.yaml（纯 Agent 审查），返回标记信息。

    Args:
        code: 股票代码
        skill_id: Skill 标识符
        session_id: Agent 会话 ID
    """
    try:
        ctx = AgentSessionRegistry.get(session_id)
        if ctx is None:
            return {"error": "会话不存在或已过期", "session_id": session_id}

        # 检查该 Skill 是否存在，以及是否仅有 Agent 审查
        skill = next((s for s in ctx.skills if s.skill_id == skill_id), None)
        if skill is None:
            return {"error": f"Skill 未找到: {skill_id}"}

        cache_key = f"{skill_id}:{code}"
        if cache_key in ctx.strategy_signals_cache:
            signal = ctx.strategy_signals_cache[cache_key]
            return {
                "skill_id": skill_id,
                "code": code,
                "phase": signal.get("phase", ""),
                "score": float(signal.get("score", 0)),
                "setup_match": signal.get("setup_match", ""),
                "evidence": signal.get("evidence", []),
                "risks": signal.get("risk_flags", []),
                "score_cap": float(signal.get("score_cap", 100.0)),
                "runtime": signal.get("runtime", "strategy_yaml"),
            }

        # 没有 strategy.yaml 的 Skill
        if skill.contract is None:
            return {
                "skill_id": skill_id,
                "code": code,
                "runtime": "agent_only",
                "note": "该 Skill 仅 Agent 审查",
                "phase": "Agent Skill 审查",
                "score": 0,
                "setup_match": "",
                "evidence": [],
                "risks": [],
                "score_cap": 100.0,
            }

        return {
            "skill_id": skill_id,
            "code": code,
            "phase": "",
            "score": 0,
            "setup_match": "",
            "evidence": [],
            "risks": [],
            "score_cap": 100.0,
            "runtime": "strategy_yaml",
            "note": "该 Skill 有 strategy.yaml 但该股票尚未执行策略计算",
        }
    except Exception as e:
        return {"error": f"查询策略信号失败: {e}", "skill_id": skill_id, "code": code}


@tool
def audit_data_quality(code: str, session_id: str) -> dict:
    """审计个股数据质量。

    统计 OBSERVED / OBSERVABLE / SYNTHETIC 特征占比，计算
    authenticity_score，列出缺失的关键字段。

    参考 feature_builder.build_features 的 _tag_features()
    与 research_context 的数据完整性评分逻辑。

    Args:
        code: 股票代码
        session_id: Agent 会话 ID
    """
    try:
        ctx = AgentSessionRegistry.get(session_id)
        if ctx is None:
            return {"error": "会话不存在或已过期", "session_id": session_id}

        # 尝试从 stocks_cache 获取已构建的股票特征
        stock = ctx.stocks_cache.get(code) if ctx.stocks_cache else None
        db = ctx.db

        # 查询 UnifiedQuote
        quote_row = db.scalar(
            select(UnifiedQuote)
            .where(UnifiedQuote.code == code)
            .order_by(UnifiedQuote.trade_date.desc())
            .limit(1)
        )
        if quote_row is None and stock is None:
            return {"error": "未找到该股票数据", "code": code}

        import json as _json

        quote_data = _json.loads(quote_row.quote_json) if quote_row and quote_row.quote_json else {}

        # 统计特征来源
        missing_fields: list[str] = []
        observed_count = 0
        synthetic_count = 0
        total_features = 0

        # -- 从数据库字段判定 OBSERVED 特征 --
        # 日K直出字段（总是 OBSERVED）
        bar_fields = {
            "price", "pct", "open", "high", "low", "close",
            "volume", "amount", "turnover_rate",
            "ma5", "ma10", "ma20", "ma60",
        }
        for field in bar_fields:
            total_features += 1
            observed_count += 1  # 日K 或 quote 总是有这些字段

        # 量能与趋势字段（从日K派生）
        derived_fields = [
            "dist20", "dist60", "vol5_20", "off_high_pct",
            "max_dd20", "upper_shadow_count20", "limit_up_days20",
        ]
        for field in derived_fields:
            total_features += 1
            observed_count += 1  # 均可从日K计算

        # -- OBSERVABLE 字段（来自外部数据源）--
        observable_fields = {
            "main_net_inflow_yi": "main_net_inflow",
            "pe_ttm": "valuation",
            "industry": "industry",
        }
        for field, gap_name in observable_fields.items():
            total_features += 1
            value = quote_data.get(field) if quote_data else None
            if value is None or value == "":
                missing_fields.append(gap_name)
            else:
                observed_count += 1  # 有值则计入

        # -- SYNTHETIC 字段（始终估算）--
        synthetic_keys = [
            "profit_ratio", "avg_cost", "concentration_90",
            "chip_health", "capital_flow_score", "fundamental_score",
            "intelligence_score", "sentiment_summary",
        ]
        for key in synthetic_keys:
            total_features += 1
            synthetic_count += 1

        # 检查关键字段
        critical_missing = {"news", "announcements", "dragon_tiger"}
        for gap in critical_missing:
            if gap not in missing_fields:
                missing_fields.append(gap)

        authenticity_score = round(observed_count / max(1, total_features), 4)
        observed_ratio = round(observed_count / max(1, total_features), 4)
        synthetic_ratio = round(synthetic_count / max(1, total_features), 4)

        # 构建来源摘要
        quote_sources = int(quote_row.source_count if quote_row else 0)
        quote_quality = float(quote_row.quality_score if quote_row else 0)
        if quote_sources >= 3:
            source_summary = f"多源验证 ({quote_sources} 源，质量 {quote_quality})"
        elif quote_sources >= 2:
            source_summary = f"双源确认 ({quote_sources} 源，质量 {quote_quality})"
        elif quote_sources == 1:
            source_summary = "单源报价，存在偏差风险"
        else:
            source_summary = "本地日K缓存"

        return {
            "code": code,
            "authenticity_score": authenticity_score,
            "observed_ratio": observed_ratio,
            "synthetic_ratio": synthetic_ratio,
            "missing_fields": sorted(set(missing_fields)),
            "source_summary": source_summary,
            "total_features": total_features,
        }
    except Exception as e:
        return {"error": f"审计数据质量失败: {e}", "code": code}


@tool
def query_stock_history(code: str, days: int, session_id: str) -> dict:
    """查询个股近期日K数据。

    返回最近 N 天的 OHLCV 和换手率，供 Agent 判断趋势、龙头地位。

    Args:
        code: 股票代码
        days: 查询天数（建议 5-60）
        session_id: Agent 会话 ID
    """
    try:
        ctx = AgentSessionRegistry.get(session_id)
        if ctx is None:
            return {"error": "会话不存在或已过期", "session_id": session_id}

        db = ctx.db
        bars = (
            db.scalars(
                select(DailyBar)
                .where(DailyBar.code == code)
                .order_by(DailyBar.trade_date.desc())
                .limit(max(1, min(days, 120)))
            )
            .all()
        )
        if not bars:
            return {"error": "未找到该股票的日K数据", "code": code}

        # 按日期升序
        bars = list(reversed(bars))
        history = []
        for bar in bars:
            history.append({
                "trade_date": bar.trade_date,
                "open": round(float(bar.open), 3),
                "high": round(float(bar.high), 3),
                "low": round(float(bar.low), 3),
                "close": round(float(bar.close), 3),
                "volume": float(bar.volume or 0),
                "pct_chg": round(float(bar.pct_chg or 0), 3),
                "turnover_rate": round(float(bar.turnover_rate or 0), 3),
            })

        return {
            "code": code,
            "count": len(history),
            "history": history,
        }
    except Exception as e:
        return {"error": f"查询股票历史数据失败: {e}", "code": code}


# ── 内部工具函数 ──────────────────────────────────────────────────────


def _upper_shadow_count(bars_20: list) -> int:
    """统计 20 日内上影线天数。"""
    count = 0
    for bar in bars_20:
        high = float(getattr(bar, "high", bar["high"]) if isinstance(bar, DailyBar) else bar["high"])
        close = float(getattr(bar, "close", bar["close"]) if isinstance(bar, DailyBar) else bar["close"])
        open_ = float(getattr(bar, "open", bar["open"]) if isinstance(bar, DailyBar) else bar["open"])
        low = float(getattr(bar, "low", bar["low"]) if isinstance(bar, DailyBar) else bar["low"])
        body = abs(close - open_)
        upper = high - max(close, open_)
        shadow_range = high - low
        if shadow_range > 0 and body > 0 and (upper / shadow_range) >= 0.55 and body / shadow_range <= 0.4:
            count += 1
    return count


def _detect_missing(quote_data: dict, bars: list) -> list[str]:
    """检测缺失的关键数据字段。"""
    missing: list[str] = []
    if not quote_data.get("industry") or quote_data.get("industry") == "未分类":
        missing.append("industry")
    if not quote_data.get("main_net_inflow_yi"):
        missing.append("main_net_inflow")
    if not quote_data.get("pe_ttm"):
        missing.append("valuation")
    if not bars or len(bars) < 30:
        missing.append("kline_history")
    missing.append("news")
    missing.append("announcements")
    missing.append("dragon_tiger")
    return sorted(missing)
