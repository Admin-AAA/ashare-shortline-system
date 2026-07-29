"""未最终结算推荐实时盈亏（实盘计算）。

对 pending / provisional（未最终结算）推荐，以 rt_k 现价计算浮盈。盘中推荐按生成时
保存的实时推荐价入场，避免全天 OHLC 倒灌推荐前行情；盘后推荐从下一交易日开始才复用
outcome 结算同款的 ``_resolve_entry`` 判定入场价。**纯展示，不落库、不改真实结算**——
盘后 ``label_pending_outcomes`` 口径不受影响。

数据源：rt_k 经统一 Router（首选 xiaodefa），见 ``TushareProviderRouter.DEFAULT_API_ROUTES``。
"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.engine.trading_calendar import latest_allowed_quote_date, normalize_trade_date
from app.models.tables import DailyKbar, Recommendation, RecommendationOutcome, RecommendationRun
from app.services.outcome_simulation import (
    MAX_ENTRY_GAP_PCT,
    OUTCOME_PENDING,
    OUTCOME_PROVISIONAL,
    _is_intraday_recommendation,
    _loads,
    _resolve_entry,
)

logger = logging.getLogger(__name__)

# 进程内短缓存：一次计算要对 pending 列表批量拉 rt_k，前端高频刷新时按 TTL 复用，
# 避免反复打上游。以「pending 代码集」为 key 不现实（每次略变），改为整批结果 TTL 缓存。
_CACHE_TTL_SECONDS = 20.0
_cache_lock = None  # 惰性初始化，避免模块导入即建锁
_cache: dict[str, Any] = {"fetched_at": 0.0, "key": None, "value": None}


def _get_lock():
    global _cache_lock
    if _cache_lock is None:
        import threading

        _cache_lock = threading.Lock()
    return _cache_lock


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _exchange_from_code(code: str) -> str:
    return "SH" if code.startswith(("6", "9")) else "SZ"


def list_pending_realtime_pnl(db: Session, *, use_cache: bool = True) -> dict[str, Any]:
    """对全部未最终结算推荐实盘计算实时浮盈，返回按 run/trade_date 分组前的扁平列表。

    返回::
        {
          "computed_at": "...",        # 本次实际拉取 rt_k 的时刻（缓存命中时为缓存时刻）
          "cached": bool,              # 是否命中 TTL 缓存
          "items": [ {...}, ... ],     # 见 _compute_one
        }
    """
    recs = _load_pending_recs(db)
    codes = sorted({str(r.code) for r in recs})
    cache_key = ",".join(codes)

    if use_cache:
        with _get_lock():
            if (
                _cache["value"] is not None
                and _cache["key"] == cache_key
                and (time.time() - _cache["fetched_at"]) < _CACHE_TTL_SECONDS
            ):
                cached_value = dict(_cache["value"])
                cached_value["cached"] = True
                return cached_value

    quotes = _fetch_rtk_quotes(codes)
    items = compute_realtime_pnl_items(db, recs, quotes)
    result = {
        "computed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cached": False,
        "items": items,
    }
    with _get_lock():
        _cache["fetched_at"] = time.time()
        _cache["key"] = cache_key
        _cache["value"] = result
    return result


def compute_realtime_pnl_items(
    db: Session,
    recs: list[Recommendation],
    quotes: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """使用调用方已取得的实时行情计算未结算推荐盈亏，不再次访问行情源。"""
    signal_bars = _load_signal_bars(db, recs)
    intermediate_bars = _load_intermediate_bars(db, recs)
    return [
        _compute_one(
            rec,
            quotes.get(str(rec.code)),
            signal_bars.get((str(rec.code), str(rec.trade_date))),
            intermediate_bars.get(str(rec.code)),
        )
        for rec in recs
    ]


def _load_pending_recs(db: Session) -> list[Recommendation]:
    """全部正式 run 的未最终结算推荐（无 outcome 行、pending 或 provisional）。"""
    stmt = (
        select(Recommendation)
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .outerjoin(
            RecommendationOutcome,
            RecommendationOutcome.recommendation_id == Recommendation.recommendation_id,
        )
        .where(
            RecommendationRun.run_mode == "formal",
            or_(
                RecommendationOutcome.recommendation_id.is_(None),
                RecommendationOutcome.outcome_label.in_((OUTCOME_PENDING, OUTCOME_PROVISIONAL)),
            ),
        )
        .order_by(Recommendation.trade_date.desc(), Recommendation.final_score.desc())
    )
    return list(db.scalars(stmt).unique().all())


def _fetch_rtk_quotes(codes: list[str]) -> dict[str, dict[str, Any]]:
    """批量拉当日实时 OHLC；rt_k 缺失时用 realtime_quote 补齐。"""
    if not codes:
        return {}
    from app.services.tushare_client import get_pro

    ts_codes = [f"{c}.{_exchange_from_code(c)}" for c in codes]
    quotes: dict[str, dict[str, Any]] = {}
    try:
        df = get_pro().rt_k(ts_code=",".join(ts_codes))
    except Exception as exc:
        logger.warning("rt_k 实时盈亏拉取失败: %s", str(exc)[:200])
        df = None
    if df is not None and not df.empty:
        quote_date = latest_allowed_quote_date(date.today())
        for _, row in df.iterrows():
            code6 = str(row.get("ts_code") or "").split(".")[0]
            if not code6:
                continue
            quotes[code6] = {
                "open": _f(row.get("open")),
                "high": _f(row.get("high")),
                "low": _f(row.get("low")),
                "close": _f(row.get("close")),  # rt_k close = 现价
                "pre_close": _f(row.get("pre_close")),
                "vol": _f(row.get("vol")),
                "amount": _f(row.get("amount")),
                "name": str(row.get("name") or "").strip(),
                # rt_k 不返回行情日期；按本地最新可用报价日标记，避免周末把周五
                # 收盘快照合成为周六行情。
                "trade_date": quote_date,
            }

    missing_codes = [code for code in codes if code not in quotes]
    if missing_codes:
        from app.services.realtime_quote_client import fetch_realtime_quotes

        fallback_rows = fetch_realtime_quotes(missing_codes, use_cache=False)
        for code6, row in fallback_rows.items():
            close = _f(row.get("price") or row.get("close"))
            if close <= 0:
                continue
            quotes[code6] = {
                "open": _f(row.get("open")),
                "high": _f(row.get("high")),
                "low": _f(row.get("low")),
                "close": close,
                "pre_close": _f(row.get("pre_close")),
                "vol": _f(row.get("volume") or row.get("vol")),
                "amount": _f(row.get("amount")),
                "name": str(row.get("name") or "").strip(),
                "trade_date": normalize_trade_date(row.get("trade_date")),
            }
    return quotes


def _load_signal_bars(db: Session, recs: list[Recommendation]) -> dict[tuple[str, str], DailyKbar]:
    """加载每个推荐的 signal_bar（trade_date 当日日K），供 _resolve_entry 判盘中/盘后。"""
    pairs = {(str(r.code), str(r.trade_date)) for r in recs}
    if not pairs:
        return {}
    codes = sorted({c for c, _ in pairs})
    dates = sorted({d for _, d in pairs})
    bars = db.scalars(
        select(DailyKbar).where(DailyKbar.code.in_(codes), DailyKbar.trade_date.in_(dates))
    ).all()
    return {(str(b.code), str(b.trade_date)): b for b in bars}


def _load_intermediate_bars(db: Session, recs: list[Recommendation]) -> dict[str, list[DailyKbar]]:
    """加载每个推荐 trade_date 之后（不含）到今天的真实日K。

    实时浮盈此前只喂一根今日合成 bar 给 _resolve_entry，pending 滞留 2+ 个交易日（结算漏跑
    /日K同步滞后等真实复发状态）时，真实入场日（T+1）被误当成今日，入场价/浮盈系统性失真。
    加载这段真实日K让 _resolve_entry 用真实入场日判定；今日仍用 rt_k 合成 bar（当日尚未收盘落库）。
    """
    pairs = [(str(r.code), str(r.trade_date)) for r in recs]
    if not pairs:
        return {}
    today_str = date.today().isoformat()
    rows: dict[str, list[DailyKbar]] = {}
    # 按 (code, signal_date) 批量取区间，避免逐股查询
    codes = sorted({c for c, _ in pairs})
    bars = db.scalars(
        select(DailyKbar).where(
            DailyKbar.code.in_(codes),
            DailyKbar.trade_date > min(d for _, d in pairs),
            DailyKbar.trade_date < today_str,
        )
    ).all()
    by_code: dict[str, list[DailyKbar]] = {}
    for b in bars:
        by_code.setdefault(str(b.code), []).append(b)
    for code, signal_date in pairs:
        code_bars = sorted(
            (b for b in by_code.get(code, []) if str(b.trade_date) > signal_date),
            key=lambda b: str(b.trade_date),
        )
        rows[code] = code_bars
    return rows


def _compute_one(
    rec: Recommendation,
    quote: dict[str, Any] | None,
    signal_bar: DailyKbar | None,
    intermediate_bars: list[DailyKbar] | None = None,
) -> dict[str, Any]:
    """对单笔 pending 推荐实盘算实时浮盈。

    quote 为 rt_k 当日实时 OHLC；为 None 时降级 unavailable。盘后推荐在下一交易日
    行情出现前返回 waiting；盘中推荐按 recommend_price 作为推荐时点的因果入场价。
    intermediate_bars 为 trade_date 次日到今日（不含）的真实日K，避免滞留 pending
    把真实入场日误判为今日（#5）。
    """
    base = {
        "recommendation_id": rec.recommendation_id,
        "code": str(rec.code),
        "trade_date": str(rec.trade_date),
        "recommend_price": _f(getattr(rec, "recommend_price", None)),
    }
    if not quote or quote.get("close", 0) <= 0:
        return {**base, "status": "unavailable", "reason": "实时行情不可用（rt_k 无数据）"}

    signal_date = str(rec.trade_date)
    quote_date = (
        normalize_trade_date(quote.get("trade_date"))
        or latest_allowed_quote_date(date.today())
    )
    intraday = _is_intraday_recommendation(rec)
    # 所有推荐统一从下一交易日开始入场。rt_k 在推荐日仍返回当天 OHLC；
    # 当日 OHLC；若把它作为 future_bar，会虚构“推荐前已入场”和实时盈亏。
    if not intraday and quote_date <= signal_date:
        return {
            **base,
            "status": "waiting",
            "reason": "盘后推荐，等待下一交易日入场",
            "now_price": quote["close"],
            "day_high": quote["high"],
            "day_low": quote["low"],
            "pre_close": quote.get("pre_close"),
        }

    # rt_k 已带 pre_close：据此反推 pct_chg，补齐 is_one_word_limit_up_bar 反推前收所需
    # （此前合成 bar 缺 pct_chg，使一字涨停「买不进」拦截恒失效，#2）。
    pre_close = _f(quote.get("pre_close"))
    pct_chg = round((quote["close"] - pre_close) / pre_close * 100, 4) if pre_close > 0 else 0.0
    today_bar = DailyKbar(
        code=str(rec.code),
        trade_date=quote_date,
        open=quote["open"],
        high=quote["high"],
        low=quote["low"],
        close=quote["close"],
        volume=quote.get("vol", 0.0),
        amount=quote.get("amount", 0.0),  # 真实成交量额，流动性检查需要 vol/amount>0
        pct_chg=pct_chg,
    )
    # future_bars：trade_date 之后到今日的全部 bar（真实历史 + 今日合成），让 _resolve_entry
    # 按真实入场日判定，而非把今日当入场日。
    future_bars = [
        bar
        for bar in (intermediate_bars or [])
        if signal_date < str(bar.trade_date) < quote_date
    ] + [today_bar]
    feature_snapshot = _loads(getattr(rec, "feature_snapshot_json", None), {})

    if intraday:
        entry_price = _f(getattr(rec, "recommend_price", None))
        if entry_price <= 0:
            return {**base, "status": "unavailable", "reason": "盘中推荐价无效"}
        entry = {
            "triggered": True,
            "price": entry_price,
            "date": signal_date,
            "reason": "intraday_recommend_price_entry",
        }
    else:
        try:
            entry = _resolve_entry(rec, feature_snapshot, future_bars, 5, signal_bar=signal_bar)
        except Exception as exc:
            logger.warning("实时盈亏入场判定失败 %s: %s", rec.code, str(exc)[:160])
            return {**base, "status": "unavailable", "reason": f"入场判定异常: {str(exc)[:80]}"}

    now_price = quote["close"]
    if not entry.get("triggered"):
        # 未触发买点：报现价与最近买点的距离，便于判断「还差多少能入场」
        targets = [
            t for t in (_f(feature_snapshot.get("ideal_buy")), _f(feature_snapshot.get("secondary_buy")))
            if t > 0
        ]
        nearest = min(targets, key=lambda t: abs(t - now_price)) if targets else None
        return {
            **base,
            "status": "watching",
            "reason": str(entry.get("reason") or "买点未触发"),
            "now_price": now_price,
            "nearest_buy_target": nearest,
            "distance_to_target_pct": (
                round((now_price - nearest) / nearest * 100, 2) if nearest else None
            ),
            "day_high": quote["high"],
            "day_low": quote["low"],
            "pre_close": quote.get("pre_close"),
        }

    entry_price = _f(entry.get("price"))
    if entry_price <= 0:
        return {**base, "status": "unavailable", "reason": "入场价无效"}

    # 跳空高开 > MAX_ENTRY_GAP_PCT 放弃入场：_simulate_outcome 的 gap 拦截在 _resolve_entry 之外，
    # 实时路径只调 _resolve_entry 会绕过它（#3）。此处复用同一阈值补齐。
    recommend_price = _f(getattr(rec, "recommend_price", None))
    if recommend_price > 0 and entry_price > 0:
        gap_pct = (entry_price - recommend_price) / recommend_price * 100
        if gap_pct > MAX_ENTRY_GAP_PCT:
            return {
                **base,
                "status": "watching",
                "reason": f"跳空高开 {gap_pct:.1f}% > {MAX_ENTRY_GAP_PCT:.0f}% 阈值，放弃入场",
                "now_price": now_price,
                "day_high": quote["high"],
                "day_low": quote["low"],
                "pre_close": quote.get("pre_close"),
            }

    # 跨除权修正：entry_price 来自推荐日附近，now_price 是今日现价，二者之间若发生除权，
    # 原始价格比值会算出虚假盈亏（10送10 显示 -50%）。用 pre_close 比值修正除权跳变（#4）。
    pnl_pct = _adjusted_pnl_pct(entry_price, now_price, future_bars, pre_close)

    return {
        **base,
        "status": "holding",
        "reason": str(entry.get("reason") or ""),
        "entry_price": entry_price,
        "entry_date": str(entry.get("date") or quote_date),
        "now_price": now_price,
        "pnl_pct": pnl_pct,
        "day_high": quote["high"],
        "day_low": quote["low"],
        "pre_close": quote.get("pre_close"),
    }


def _adjusted_pnl_pct(
    entry_price: float,
    now_price: float,
    future_bars: list[DailyKbar],
    today_pre_close: float,
) -> float:
    """跨除权日的浮盈修正：用 pct_chg 链累乘替代裸价比值（#4）。

    若 entry 日到今日之间无除权，pct_chg 链与裸价比值近似相等；若发生除权（10送10 等），
    pct_chg 已含除权调整，累乘即真实收益。entry 当日（若在 future_bars 中）的 pct_chg
    计 entry→close 的当日部分，其余日累乘到今日现价。

    实时展示口径与 _simulate_outcome 的除权 pct_chg 链重算对齐（outcome_simulation.py:300-330），
    保证盘中显示与盘后真实结算不在除权日分叉。
    """
    raw = (now_price - entry_price) / entry_price * 100
    if not future_bars or entry_price <= 0 or now_price <= 0:
        return round(raw, 2)
    # 检测除权日：相邻 bar 的 open/prev_close 跳变 > 20% 但 pct_chg 在涨停幅度内
    has_ex_right = False
    prev_close = entry_price
    for bar in future_bars:
        cur_open = _f(getattr(bar, "open", 0))
        cur_pct = _f(getattr(bar, "pct_chg", 0))
        if prev_close > 0 and cur_open > 0:
            price_jump = (cur_open / prev_close - 1) * 100
            if abs(price_jump) > 20 and abs(cur_pct) < 12:  # 12% 覆盖主板/创业/科创涨停
                has_ex_right = True
                break
        prev_close = _f(getattr(bar, "close", 0)) or prev_close
    if not has_ex_right:
        return round(raw, 2)
    # pct_chg 链累乘（含今日合成 bar 的 pct_chg）
    cum = 1.0
    for bar in future_bars:
        cum *= (1 + _f(getattr(bar, "pct_chg", 0)) / 100.0)
    return round((cum - 1) * 100, 2)
