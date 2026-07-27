"""未结算推荐实时盈亏（实盘计算）。

对 pending（未结算）推荐，用 rt_k 当日实时 OHLC 合成一根当日临时日K，
复用 outcome 结算同款的 ``_resolve_entry`` 判定入场价（含买点命中/开盘回退/
gap 过滤/一字跌停 block 等边界），再以 rt_k 现价算浮盈。**纯展示，不落库、
不改真实结算**——盘后 ``label_pending_outcomes`` 口径不受影响。

数据源：rt_k 经统一 Router（首选 xiaodefa），见 ``TushareProviderRouter.DEFAULT_API_ROUTES``。
"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.tables import DailyKbar, Recommendation, RecommendationOutcome, RecommendationRun
from app.services.outcome_simulation import OUTCOME_PENDING, _loads, _resolve_entry

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
    """对全部 pending 推荐实盘计算实时浮盈，返回按 run/trade_date 分组前的扁平列表。

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
    signal_bars = _load_signal_bars(db, recs)

    items = [
        _compute_one(rec, quotes.get(str(rec.code)), signal_bars.get((str(rec.code), str(rec.trade_date))))
        for rec in recs
    ]
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


def _load_pending_recs(db: Session) -> list[Recommendation]:
    """全部正式 run 的 pending 推荐（无 outcome 行 或 outcome_label=pending）。"""
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
                RecommendationOutcome.outcome_label == OUTCOME_PENDING,
            ),
        )
        .order_by(Recommendation.trade_date.desc(), Recommendation.final_score.desc())
    )
    return list(db.scalars(stmt).unique().all())


def _fetch_rtk_quotes(codes: list[str]) -> dict[str, dict[str, Any]]:
    """经统一 Router 批量拉 rt_k 当日实时 OHLC，返回 {code6: {open,high,low,close,...}}。"""
    if not codes:
        return {}
    from app.services.tushare_client import get_pro

    ts_codes = [f"{c}.{_exchange_from_code(c)}" for c in codes]
    try:
        df = get_pro().rt_k(ts_code=",".join(ts_codes))
    except Exception as exc:
        logger.warning("rt_k 实时盈亏拉取失败: %s", str(exc)[:200])
        return {}
    if df is None or df.empty:
        return {}
    quotes: dict[str, dict[str, Any]] = {}
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


def _compute_one(
    rec: Recommendation,
    quote: dict[str, Any] | None,
    signal_bar: DailyKbar | None,
) -> dict[str, Any]:
    """对单笔 pending 推荐实盘算实时浮盈。

    quote 为 rt_k 当日实时 OHLC；为 None 时降级 unavailable。
    """
    base = {
        "recommendation_id": rec.recommendation_id,
        "code": str(rec.code),
        "trade_date": str(rec.trade_date),
        "recommend_price": _f(getattr(rec, "recommend_price", None)),
    }
    if not quote or quote.get("close", 0) <= 0:
        return {**base, "status": "unavailable", "reason": "实时行情不可用（rt_k 无数据）"}

    today_str = date.today().isoformat()
    # 用 rt_k 当日 OHLC 合成当日临时日K，作为 future_bars 喂给 _resolve_entry
    today_bar = DailyKbar(
        code=str(rec.code),
        trade_date=today_str,
        open=quote["open"],
        high=quote["high"],
        low=quote["low"],
        close=quote["close"],
        volume=quote.get("vol", 0.0),
        amount=quote.get("amount", 0.0),  # 真实成交量额，流动性检查需要 vol/amount>0
    )
    feature_snapshot = _loads(getattr(rec, "feature_snapshot_json", None), {})

    try:
        entry = _resolve_entry(rec, feature_snapshot, [today_bar], 5, signal_bar=signal_bar)
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

    pnl_pct = round((now_price - entry_price) / entry_price * 100, 2)
    return {
        **base,
        "status": "holding",
        "reason": str(entry.get("reason") or ""),
        "entry_price": entry_price,
        "entry_date": str(entry.get("date") or today_str),
        "now_price": now_price,
        "pnl_pct": pnl_pct,
        "day_high": quote["high"],
        "day_low": quote["low"],
        "pre_close": quote.get("pre_close"),
    }
