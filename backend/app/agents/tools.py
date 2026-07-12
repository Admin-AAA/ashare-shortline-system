"""
Agno Agent 工具函数。

用 Agno @tool 装饰器包装本地数据查询工具。所有 tool 函数只接受简单类型
参数（str, int），通过 session_id 从 AgentSessionRegistry 获取 db session，
返回 JSON-serializable 的 dict。

设计约束:
  - 不向 LLM 暴露 db Session 或 ORM 对象
  - 所有异常 catch 后返回 error dict，不 raise
  - 只做数据查询和简单统计，不做复杂特征计算
  - 会话不可用时自动降级：直接用 SessionLocal() 查库，不返回致命错误
"""

from contextlib import contextmanager
from typing import Any

from sqlalchemy import desc, func, select

from app.agents.session_registry import AgentSessionRegistry
from app.db.session import SessionLocal
from app.models.tables import (
    DailyKbar,
    DragonTiger,
    LimitUpBoard,
    Stock,
    StockQuote,
)
from app.services.indicator_computer import (
    bias as _bias,
    ma as _ic_ma,
    strict_upper_shadow_count as _upper_shadow_count,
)


# ── Agno tool 装饰器 ──────────────────────────────────────────────────

try:
    from agno.tools import tool
except ImportError:
    # 开发期兼容：agno 未安装时提供一个 no-op 装饰器
    def tool(func):  # type: ignore[no-redef]
        return func


# ── 内部辅助函数 ──────────────────────────────────────────────────────

@contextmanager
def _get_db(session_id: str):
    """获取 DB session。优先从 Agent 会话获取，不可用时直接创建。"""
    ctx = AgentSessionRegistry.get(session_id)
    if ctx is not None:
        with ctx.session_factory() as db:
            yield db, ctx
    else:
        db = SessionLocal()
        try:
            yield db, None
        finally:
            db.close()


def _get_cache_or_none(session_id: str):
    """获取 Agent 会话的缓存数据，不存在时返回 None。"""
    ctx = AgentSessionRegistry.get(session_id)
    if ctx is None:
        return None
    return ctx


def _num(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


# `_ma` 已收口到 indicator_computer.ma：tools 历史上短序列（len<window）取最后一个值，
# 经关键字参数 short="last" 复用唯一实现，保留原调用点写法 `_ma(values, window)` 与行为。
def _ma(values: list[float], window: int) -> float:
    return _ic_ma(values, window, short="last")


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
            return {
                "trade_date": "",
                "sample_count": 0,
                "up_count": 0,
                "down_count": 0,
                "limit_up_count": 0,
                "limit_down_count": 0,
                "main_industries": [],
                "breadth": {"regime": "未知", "risk": "未知", "hot_theme": "未知"},
                "_note": "Agent 会话缓存不可用，市场快照数据依赖 prompt 中的预计算摘要",
            }

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
    （DailyKbar + StockQuote）查询并构建。

    返回价格、技术指标、量能、风险、资金等结构化字段。

    Args:
        code: 股票代码（如 600519）
        session_id: Agent 会话 ID
    """
    try:
        ctx = AgentSessionRegistry.get(session_id)

        # 缓存命中（仅当会话可用时）
        if ctx is not None and code in ctx.stocks_cache:
            cached = dict(ctx.stocks_cache[code])
            cached.pop("_bars", None)
            cached.pop("dsa_profile", None)
            return cached

        with _get_db(session_id) as (db, ctx2):
            ctx2 = ctx2 or ctx  # 优先用 with 返回的 ctx2，回退到外层 ctx

            # 查询最新一条 StockQuote
            quote_row = db.scalar(
                select(StockQuote)
                .where(StockQuote.code == code)
                .order_by(StockQuote.trade_date.desc())
                .limit(1)
            )
            if quote_row is None:
                return {"error": "未找到该股票数据", "code": code}

            quote_data = {
                "code": quote_row.code,
                "name": quote_row.name,
                "price": float(quote_row.price or 0),
                "pct": float(quote_row.pct or 0),
                "turnover_rate": float(quote_row.turnover_rate or 0),
                "amount_yi": float(quote_row.amount_yi or 0),
                "free_mv_yi": float(quote_row.free_mv_yi or 0) if quote_row.free_mv_yi else None,
                "pe_ttm": float(quote_row.pe_ttm) if quote_row.pe_ttm else None,
                "industry": quote_row.industry,
            }

            # 查询最近 60 条日 K
            bars = (
                db.scalars(
                    select(DailyKbar)
                    .where(DailyKbar.code == code)
                    .order_by(DailyKbar.trade_date.desc())
                    .limit(60)
                )
                .all()
            )
            if not bars:
                return {"error": "未找到该股票的日K数据", "code": code}

            # 按日期升序排列
            bars = list(reversed(bars))

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
            vol5_20 = float(latest.vol_ratio_5) if latest.vol_ratio_5 is not None else (
                _ma(volumes, 5) / ma_vol20 if ma_vol20 else 1.0
            )

            # primary_excess20 用 chg20 近似：相对于市场均值的超额
            chg20 = ((closes[-1] - closes[-21]) / closes[-21] * 100) if len(closes) >= 21 else 0.0

            # 最大回撤和上影线
            max_high20 = max(highs[-20:]) if len(highs) >= 20 else max(highs)
            off_high_pct = (price - max_high20) / max_high20 * 100 if max_high20 else 0.0

            max_dd20: float = 0.0
            if latest.max_dd20 is not None:
                max_dd20 = float(latest.max_dd20)
            else:
                peak = closes[-min(20, len(closes))]
                for i in range(len(closes) - min(20, len(closes)) + 1, len(closes)):
                    peak = max(peak, closes[i])
                    dd = (closes[i] - peak) / peak * 100 if peak else 0
                    max_dd20 = min(max_dd20, dd)

            upper_shadow_count20: int = 0
            if latest.upper_shadow_count20 is not None:
                upper_shadow_count20 = int(latest.upper_shadow_count20 or 0)
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

            # ── 基础短线策略关键字段（从 stocks_cache 补充，防止 Agent 凭空推断 ──
            stock_cached = ctx.stocks_cache.get(code) if ctx is not None and ctx.stocks_cache else None
            if stock_cached:
                for dsa_key in (
                    "support_ma5", "support_ma10", "dsa_support_score",
                    "trend_status", "ma_alignment",
                    "macd_status", "macd_signal",
                    "rsi_status", "rsi_signal",
                    "bias_ma5", "bias_ma10", "bias_ma20",
                    "volume_breakout_signal", "volume_shrink_pullback",
                    "pos20", "pos60", "pos120",
                    "is_main_theme", "is_shortline_leader_candidate", "shortline_leader_score",
                    "market_sentiment_phase",
                    "limit_up_overheat_score", "limit_up_overheat_level",
                    "sector_ladder_completeness_score", "sector_ladder_level",
                    "sector_ladder_limit_up_count", "sector_ladder_height",
                    "capital_resonance_score", "capital_resonance_signal", "capital_resonance_source",
                    "is_20cm_stock", "twenty_cm_risk_score", "twenty_cm_risk_gate", "twenty_cm_risk_reason",
                    "dragon_tiger_buy_dispersion", "dragon_tiger_inst_buy_ratio",
                    "shortline_factor_explanations",
                ):
                    if dsa_key in stock_cached:
                        result[dsa_key] = stock_cached[dsa_key]

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
            return {
                "skill_id": skill_id,
                "code": code,
                "runtime": "session_unavailable",
                "note": "Agent 会话缓存不可用；使用 prompt 预计算数据进行审查",
                "phase": "",
                "score": 0,
                "setup_match": "",
                "evidence": [],
                "risks": [],
                "score_cap": 100.0,
            }

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
        stock = None
        ctx = AgentSessionRegistry.get(session_id)
        if ctx is not None:
            stock = ctx.stocks_cache.get(code) if ctx.stocks_cache else None

        with _get_db(session_id) as (db, _ctx):
            import json as _json

            # 查询 StockQuote
            quote_row = db.scalar(
                select(StockQuote)
                .where(StockQuote.code == code)
                .order_by(StockQuote.trade_date.desc())
                .limit(1)
            )
            if quote_row is None and stock is None:
                return {"error": "未找到该股票数据", "code": code}

            quote_data = {
                "code": quote_row.code,
                "name": quote_row.name,
                "price": float(quote_row.price or 0),
                "pct": float(quote_row.pct or 0),
                "pe_ttm": float(quote_row.pe_ttm) if quote_row.pe_ttm else None,
                "industry": quote_row.industry,
                "main_net_inflow_yi": None,
                "free_mv_yi": float(quote_row.free_mv_yi or 0) if quote_row.free_mv_yi else None,
            } if quote_row else {}

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
        with _get_db(session_id) as (db, _ctx):
            bars = (
                db.scalars(
                    select(DailyKbar)
                    .where(DailyKbar.code == code)
                    .order_by(DailyKbar.trade_date.desc())
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


@tool
def query_dragon_tiger(code: str, session_id: str) -> dict:
    """查询个股最新龙虎榜数据。

    从 DragonTiger 标准化表获取最新交易日的龙虎榜上榜信息，
    从 DragonTiger 标准化表获取龙虎榜上榜信息及买卖席位。

    返回买入席位、卖出席位、净买入额、机构参与度、游资标记等。

    Args:
        code: 股票代码（如 600519）
        session_id: Agent 会话 ID
    """
    try:
        with _get_db(session_id) as (db, _ctx):

            # 查询最新 DragonTiger 标准化记录
            dt_row = db.scalar(
                select(DragonTiger)
                .where(DragonTiger.code == code)
                .order_by(DragonTiger.trade_date.desc())
                .limit(1)
            )
            if dt_row is None:
                return {"error": "未找到该股票的龙虎榜数据", "code": code}

            # 字段提取
            buy_amount = round(float(dt_row.buy_amount_yi or 0), 4)
            sell_amount = round(float(dt_row.sell_amount_yi or 0), 4)
            net_amount = round(float(dt_row.net_amount_yi or 0), 4)
            institution_net_buy = round(float(dt_row.institution_net_buy_yi or 0), 4)

            buy_seats: list[dict] = []
            sell_seats: list[dict] = []

            # 用标准化表的 top_buy_broker / top_sell_broker 获取席位信息
            if not buy_seats and dt_row.top_buy_broker:
                buy_seats = [{"rank": 1, "name": dt_row.top_buy_broker, "amount_yi": buy_amount}]
            if not sell_seats and dt_row.top_sell_broker:
                sell_seats = [{"rank": 1, "name": dt_row.top_sell_broker, "amount_yi": sell_amount}]

            # 机构参与度判定
            total_amount = buy_amount + sell_amount
            institution_ratio = abs(institution_net_buy) / total_amount if total_amount > 0 else 0
            if institution_ratio >= 0.15 and institution_net_buy > 0:
                institutional_dominance = "机构主导净买入"
            elif institution_ratio >= 0.15 and institution_net_buy < 0:
                institutional_dominance = "机构主导净卖出"
            elif institution_ratio >= 0.05:
                institutional_dominance = "机构参与"
            else:
                institutional_dominance = "无明显机构参与"

            # 游资标记：识别知名游资席位关键词
            hot_money_keywords = [
                "拉萨", "东方财富证券", "国泰君安", "中信证券",
                "华泰证券", "国盛证券", "银河证券", "财通证券",
                "光大证券", "申万宏源",
            ]
            hot_money_flags: list[str] = []
            for seat in buy_seats + sell_seats:
                name = str(seat.get("name", ""))
                for kw in hot_money_keywords:
                    if kw in name and name not in hot_money_flags:
                        hot_money_flags.append(name)
            has_hot_money = any(
                any(kw in str(s.get("name", "")) for kw in hot_money_keywords)
                for s in buy_seats + sell_seats
            )

            return {
                "code": code,
                "name": dt_row.name or code,
                "trade_date": dt_row.trade_date,
                "reason": dt_row.reason or "",
                "buy_amount_yi": buy_amount,
                "sell_amount_yi": sell_amount,
                "net_amount_yi": net_amount,
                "buy_seat_count": int(dt_row.buy_seat_count or 0),
                "sell_seat_count": int(dt_row.sell_seat_count or 0),
                "institution_net_buy_yi": institution_net_buy,
                "institutional_dominance": institutional_dominance,
                "buy_seats": buy_seats,
                "sell_seats": sell_seats,
                "hot_money_flags": hot_money_flags[:5],
                "has_hot_money": has_hot_money,
            }
    except Exception as e:
        return {"error": f"查询龙虎榜数据失败: {e}", "code": code}


@tool
def query_industry_peers(code: str, session_id: str) -> dict:
    """查询同行业股票对比。

    找出与指定股票同行业的所有股票，计算该股票在同行业中的
    涨幅排名、行业涨停家数、是否为行业龙头等信息。

    Args:
        code: 股票代码（如 600519）
        session_id: Agent 会话 ID
    """
    try:
        with _get_db(session_id) as (db, _ctx):

            # 获取目标股票的最新 quote 和行业信息
            quote_row = db.scalar(
                select(StockQuote)
                .where(StockQuote.code == code)
                .order_by(StockQuote.trade_date.desc())
                .limit(1)
            )
            if quote_row is None:
                return {"error": "未找到该股票数据", "code": code}

            trade_date = quote_row.trade_date

            # 行业来源：优先 StockQuote，其次 Stock 表
            industry = quote_row.industry or ""
            if (not industry or industry == "未分类"):
                stock_row = db.scalar(select(Stock).where(Stock.code == code))
                if stock_row and stock_row.industry:
                    industry = stock_row.industry
            if not industry or industry == "未分类":
                return {"error": "该股票无行业分类信息", "code": code}

            # 获取同行业所有股票的最新 StockQuote
            peer_quotes = (
                db.execute(
                    select(StockQuote, Stock)
                    .join(Stock, StockQuote.code == Stock.code)
                    .where(Stock.industry == industry, StockQuote.code != code)
                    .order_by(StockQuote.trade_date.desc())
                )
                .all()
            )

            # 按 trade_date 去重，保留每只股票最新记录
            seen_codes: set[str] = set()
            peer_rows: list[tuple] = []
            for quote, stock in peer_quotes:
                if quote.code in seen_codes:
                    continue
                seen_codes.add(quote.code)
                if quote.trade_date == trade_date:
                    peer_rows.append((quote, stock))
                else:
                    # 非当日数据也保留（至少可提供静态信息）
                    if len(peer_rows) < 100:
                        peer_rows.append((quote, stock))

            # 解析目标股票自身数据
            self_name = quote_row.name or code
            self_pct = _num(quote_row.pct)
            self_amount_yi = _num(quote_row.amount_yi)
            self_turnover = _num(quote_row.turnover_rate)
            self_free_mv_yi = _num(quote_row.free_mv_yi)

            # 构建同业列表并排序
            peers: list[dict] = []
            all_pcts: list[float] = [self_pct]
            for quote, stock in peer_rows:
                peer_pct = _num(quote.pct)
                peer_amount = _num(quote.amount_yi)
                peer_turnover = _num(quote.turnover_rate)
                peer_mv = _num(quote.free_mv_yi)
                peers.append({
                    "code": quote.code,
                    "name": quote.name or str(stock.name),
                    "pct": round(peer_pct, 3),
                    "amount_yi": round(peer_amount, 3),
                    "turnover_rate": round(peer_turnover, 3),
                    "free_mv_yi": round(peer_mv, 3),
                })
                all_pcts.append(peer_pct)

            # 按 pct 降序排列
            peers_sorted = sorted(peers, key=lambda x: -x["pct"])
            self_rank = sum(1 for p in peers_sorted if p["pct"] > self_pct) + 1
            total_peers = len(peers_sorted) + 1  # 含自身

            # 行业涨停家数：查询同日 LimitUpBoard 并在同业中的股票数
            peer_codes = [p["code"] for p in peers] + [code]
            limit_up_rows = (
                db.scalars(
                    select(LimitUpBoard)
                    .where(
                        LimitUpBoard.code.in_(peer_codes),
                        LimitUpBoard.trade_date == trade_date,
                    )
                )
                .all()
            )
            limit_up_codes = {r.code for r in limit_up_rows}
            industry_limit_up_count = len(limit_up_codes)
            self_is_limit_up = code in limit_up_codes

            # 是否为行业龙头判断
            leader_signals: list[str] = []
            # 市值龙头
            if self_free_mv_yi > 0:
                all_mvs = [self_free_mv_yi] + [p["free_mv_yi"] for p in peers if p["free_mv_yi"] > 0]
                if all_mvs and self_free_mv_yi == max(all_mvs):
                    leader_signals.append("市值最大")
            # 成交额龙头
            if self_amount_yi > 0:
                all_amounts = [self_amount_yi] + [p["amount_yi"] for p in peers if p["amount_yi"] > 0]
                if all_amounts and self_amount_yi == max(all_amounts):
                    leader_signals.append("成交额最大")
            # 涨幅龙头（涨停且最早封板视为信号）
            if self_is_limit_up and self_rank <= 3:
                leader_signals.append("涨幅领先(涨停)")

            is_leader = len(leader_signals) >= 2
            leader_judgment = {
                "is_leader": is_leader,
                "signals": leader_signals,
                "detail": "、".join(leader_signals) if leader_signals else "无明显龙头特征",
            }

            # 同业涨幅前三
            top3 = peers_sorted[:3] if len(peers_sorted) >= 3 else peers_sorted[:len(peers_sorted)]

            return {
                "code": code,
                "name": self_name,
                "industry": industry,
                "trade_date": trade_date,
                "self_pct": round(self_pct, 3),
                "self_rank": self_rank,
                "total_peers": total_peers,
                "rank_pct": round(self_rank / max(1, total_peers) * 100, 1),
                "industry_limit_up_count": industry_limit_up_count,
                "self_is_limit_up": self_is_limit_up,
                "leader_judgment": leader_judgment,
                "top3_peers": top3,
                "peer_count": len(peers),
                "all_peers": peers_sorted[:30],  # 最多返回 30 只
            }
    except Exception as e:
        return {"error": f"查询同行业数据失败: {e}", "code": code}


# ── 内部工具函数 ──────────────────────────────────────────────────────


# `_upper_shadow_count`（严格口径：上影线/全幅 ≥ 0.55 且 实体/全幅 ≤ 0.4 且 实体 > 0，
# 兼容 ORM/dict 入参）已收口到 indicator_computer.strict_upper_shadow_count，经顶部
# import 别名复用，本地不再保留副本。


def _detect_missing(quote_data: dict, bars: list) -> list[str]:
    """检测缺失的关键数据字段。"""
    missing: list[str] = []
    if not quote_data.get("industry") or quote_data.get("industry") == "未分类":
        missing.append("industry")
    if not quote_data.get("pe_ttm"):
        missing.append("valuation")
    if not bars or len(bars) < 30:
        missing.append("kline_history")
    missing.append("capital_flow")
    missing.append("news")
    missing.append("dragon_tiger")
    return sorted(missing)
