"""行情池辅助数据集：~15 组辅助数据（limit_up_board / concept_sector(+成分股) /
capital_flow / dragon_tiger / news / margin_detail / block_trade / hsgt_flow /
hsgt_top10 / pledge / broker_recs / share_float_upcoming / fina_indicator /
auction / holder_number / holder_trade / institutional_survey）的
refresh/persist/load 三件套。

任务 5.1（需求 5.1）落地：`AuxiliaryDataset` 描述符 + 通用 upsert-by-key，收口
现状里几乎重复的 "按 key 字段查是否存在 → 存在则逐字段 setattr 更新，不存在则
insert" 持久化样板（此前 `_persist_normalized_limit_up_board` /
`_persist_normalized_concept_sector` / `_persist_normalized_capital_flow` /
`_persist_normalized_dragon_tiger` / margin_detail / hsgt_top10 / hsgt_flow 的
持久化函数几乎是同一套逻辑，只是字段拼写不同）。

**描述符覆盖范围的设计取舍**（不为了凑数字硬套用）：
- 5 组走 `_refresh_aux_data` 通用刷新壳子的（limit_up/concept_sector/dragon_tiger/
  capital_flow/news）：fetch 侧是"多源轮询 + source_health 记录"共性强，但各自的
  fetcher 签名（是否要 trade_date 过滤/是否批量优先 fallback 逐股）差异较大，
  仍保留各自 `refresh_*` 实现；只有 persist 侧的 "upsert by key" 部分用描述符收口
  （news 除外，因其 key 是二元组 (source, article_id) 且有去重与写失败回滚语义，
  形状与其余 4 组不同，未强套）。
- margin_detail / hsgt_flow：不走多源轮询（直接调 tushare_client 批量接口），
  但 persist 侧同样是 "upsert by key" 的样板（字段名与模型列名一致），套用描述符收口。
- hsgt_top10：同样不走多源轮询，但源字段名 `change` 与模型列名 `change_pct` 不一致，
  且原实现这个映射只存在于"更新已存在记录"分支（插入分支因字段名不匹配会静默丢弃该
  字段——插入与更新路径行为不对称，是原实现固有行为非本次要修的 bug）。通用描述符
  假设"row 字段名 == 模型列名"，套用会静默改变这个不对称行为，因此不套用，保留原始
  逐字段实现。
- block_trade：先删后插（非 upsert 语义，避免同交易日重复大宗记录），形状与
  upsert-by-key 不同，不套用描述符，保留原实现。
- fina_indicator：已经是 mysql_insert.on_duplicate_key_update 原子 upsert（避免
  并发唯一键冲突拖垮整条推荐管线），比 "查询后 setattr" 更强健，不应降级为
  check-then-insert 描述符，保留原实现。
- concept_sector_stock：三元 key (sector_code, code, trade_date) 且量级远大于其余
  数据集（每板块数十只成分股 × 数十板块），仍是 upsert-by-key 形状，套用描述符。
- pledge/broker_recs/share_float_upcoming/auction/holder_number/holder_trade/
  institutional_survey：这组只有 `_load_*_for_stocks` 读取（数据由别处同步脚本写
  入本表，这里只读），没有 refresh/persist 三件套，天然不适用本描述符（描述符收口
  的是"写入样板"，只读函数没有这个重复问题），保留各自查询实现。

新增一种"多源轮询 + upsert-by-key"辅助数据集时，只需：
1. 在 `AUXILIARY_DATASETS` 里注册一个 `AuxiliaryDataset(model=..., key_fields=(...))`
2. persist 函数体调用 `_upsert_by_key(db, dataset, rows)` 而不是手写 check-then-insert
不需要再复制一份「查是否存在 → 存在则逐字段更新，否则插入」的样板代码。
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.tables import (
    BlockTrade,
    CapitalFlow,
    ConceptSector,
    ConceptSectorStock,
    DragonTiger,
    HolderNumber,
    HolderTrade,
    HsgtFlow,
    HsgtTop10,
    InstitutionalSurvey,
    LimitUpBoard,
    MarginDetail,
    NewsAnnouncement,
)
from app.services.data_sources import enabled_sources
from app.engine.trading_calendar import normalize_trade_date as _normalize_trade_date
from app.services.market_pool.core import (
    _date_window_days,
    _record_source_health,
    _resolve_snapshot_trade_date,
)
from app.services import market_pool_adapters as pool_adapters

logger = logging.getLogger(__name__)

_limit_up_sources = pool_adapters.limit_up_sources
_concept_sector_sources = pool_adapters.concept_sector_sources
_capital_flow_sources = pool_adapters.capital_flow_sources
_dragon_tiger_sources = pool_adapters.dragon_tiger_sources
_news_sources = pool_adapters.news_sources
_fetch_limit_up_board = pool_adapters.fetch_limit_up_board_rows
_fetch_concept_sectors = pool_adapters.fetch_concept_sector_rows
_fetch_capital_flow = pool_adapters.fetch_capital_flow_rows
_fetch_capital_flow_batch = pool_adapters.fetch_capital_flow_batch
_fetch_dragon_tiger = pool_adapters.fetch_dragon_tiger_rows
_fetch_news = pool_adapters.fetch_news_rows


# ═════════════════════════════════════════════════════════════════════════════
# AuxiliaryDataset 描述符 + 通用 upsert-by-key（需求 5.1）
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class AuxiliaryDataset:
    """声明式描述一组"多源轮询/批量拉取 + upsert-by-key"辅助数据集。

    - `model`：ORM 模型类，用于反射列名（`__mapper__.columns.keys()`）过滤 payload。
    - `key_fields`：唯一键字段名元组，用于查重（存在则更新，不存在则插入）。
    - `touch_created_at`：更新已存在记录时是否刷新 `created_at`（沿用原实现里
      各 `_persist_normalized_*` 函数在更新分支都会 `existing.created_at = now()`
      的行为——这些表把 `created_at` 当"最后写入时间"用，而不是严格的创建时间，
      保持行为不变）。
    """

    model: type
    key_fields: tuple[str, ...]
    touch_created_at: bool = True


AUXILIARY_DATASETS: dict[str, AuxiliaryDataset] = {
    "limit_up_board": AuxiliaryDataset(model=LimitUpBoard, key_fields=("code", "trade_date")),
    "concept_sector": AuxiliaryDataset(model=ConceptSector, key_fields=("sector_code", "trade_date")),
    "concept_sector_stock": AuxiliaryDataset(model=ConceptSectorStock, key_fields=("sector_code", "code", "trade_date")),
    "capital_flow": AuxiliaryDataset(model=CapitalFlow, key_fields=("code", "trade_date")),
    "dragon_tiger": AuxiliaryDataset(model=DragonTiger, key_fields=("code", "trade_date")),
    # margin_detail / hsgt_flow 的原实现更新分支不刷新 created_at（与上面 4 组不同，
    # 上面 4 组的原 `_persist_normalized_*` 都会在更新分支显式
    # `existing.created_at = datetime.now(...)`）——保持这个差异，touch_created_at=False。
    "margin_detail": AuxiliaryDataset(model=MarginDetail, key_fields=("code", "trade_date"), touch_created_at=False),
    "hsgt_flow": AuxiliaryDataset(model=HsgtFlow, key_fields=("trade_date",), touch_created_at=False),
    # 注意：hsgt_top10 未注册——源字段 "change" 与模型列名 "change_pct" 不一致，
    # 且原实现该映射只存在于更新分支（见 `_persist_hsgt_top10` 的说明），套用通用
    # upsert 会静默改变这个不对称行为，故保留其原始逐字段实现，不在此注册描述符。
}


def _upsert_by_key(db: Session, dataset: AuxiliaryDataset, rows: list[dict[str, Any]]) -> None:
    """按 `dataset.key_fields` 查是否已存在：存在则逐字段更新，不存在则插入。

    收口现状里几乎逐字重复的 "check-then-insert" 持久化样板；不 `db.commit()`
    （提交时机由调用方决定，与原各 `_persist_normalized_*` 函数保持一致——
    有的在循环外统一 commit，有的由上层 `_refresh_aux_data` 统一 commit）。
    """
    model = dataset.model
    column_names = set(model.__mapper__.columns.keys())
    seen_batch: set[tuple] = set()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for row in rows:
        key_values = tuple(str(row.get(field_name) or "") for field_name in dataset.key_fields)
        if not all(key_values):
            continue
        if key_values in seen_batch:
            continue
        seen_batch.add(key_values)
        conditions = [
            getattr(model, field_name) == row.get(field_name) for field_name in dataset.key_fields
        ]
        existing = db.scalar(select(model).where(*conditions))
        if existing is None:
            db.add(model(**{k: v for k, v in row.items() if k in column_names}))
        else:
            for key_name, value in row.items():
                if key_name in column_names and key_name not in {"id", "created_at"}:
                    setattr(existing, key_name, value)
            if dataset.touch_created_at:
                existing.created_at = now


# ── 辅助数据池：刷新入口 ───────────────────────────────────────────────

def refresh_limit_up_board(db: Session, trade_date: str | None = None) -> list[dict[str, Any]]:
    """刷新涨停板数据。"""
    settings = get_settings()
    if not settings.enable_limit_up_fetch:
        return []
    sources = enabled_sources(db, "limit_up", _limit_up_sources(settings))
    trade_date = _resolve_snapshot_trade_date(db, trade_date)
    return _refresh_aux_data(
        db, "limit_up", sources, settings,
        fetcher=lambda source, _settings: _fetch_limit_up_board(source, _settings, trade_date=trade_date),
        normalized_persister=_persist_normalized_limit_up_board,
    )


def refresh_concept_sectors(db: Session, trade_date: str | None = None) -> list[dict[str, Any]]:
    """刷新概念板块数据。"""
    settings = get_settings()
    if not settings.enable_concept_sector_fetch:
        return []
    sources = enabled_sources(db, "concept_sector", _concept_sector_sources(settings))
    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    result = _refresh_aux_data(
        db, "concept_sector", sources, settings,
        fetcher=lambda source, _settings: _fetch_concept_sectors(source, _settings, trade_date=target_trade_date),
        normalized_persister=_persist_normalized_concept_sector,
    )
    # 额外持久化热门板块的成分股（Tushare）
    from app.services.tushare_client import fetch_concept_members

    # 检查快照交易日是否已有成分股数据，有则跳过
    existing_count = db.scalar(
        select(func.count()).select_from(ConceptSectorStock).where(
            ConceptSectorStock.trade_date == target_trade_date
        )
    ) or 0
    if existing_count > 0:
        logger.info("概念板块成分股已覆盖 %s，跳过拉取", target_trade_date)
        return result

    top_sectors = [s for s in result if s.get("heat_rank", 999) <= 60]
    if not top_sectors:
        return result

    all_stocks: list[dict[str, Any]] = []
    sector_codes = [str(s.get("sector_code") or "") for s in top_sectors]
    sector_codes = [c for c in sector_codes if c]

    timeout_seconds = max(1.0, float(getattr(settings, "concept_member_fetch_timeout_seconds", 30.0) or 30.0))
    pool = ThreadPoolExecutor(max_workers=6)
    futures = {pool.submit(fetch_concept_members, sc, target_trade_date): sc for sc in sector_codes}
    processed: set[Any] = set()
    try:
        for future in as_completed(futures, timeout=timeout_seconds):
            processed.add(future)
            try:
                stocks = future.result()
                if stocks:
                    all_stocks.extend(stocks)
            except Exception:
                pass
    except TimeoutError:
        logger.warning(
            "概念板块成分股拉取超过 %.1fs，已降级使用已完成结果：%d/%d 个板块",
            timeout_seconds,
            len(processed),
            len(futures),
        )
    finally:
        for future in futures:
            if future not in processed:
                future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)

    if all_stocks:
        _persist_concept_sector_stocks(db, all_stocks)
        db.commit()
        logger.info("概念板块成分股已持久化 %d 条，覆盖 %d 个板块", len(all_stocks), len(sector_codes))
    return result


def refresh_dragon_tiger(db: Session, trade_date: str | None = None) -> list[dict[str, Any]]:
    """刷新龙虎榜数据。"""
    settings = get_settings()
    if not settings.enable_dragon_tiger_fetch:
        return []
    sources = enabled_sources(db, "dragon_tiger", _dragon_tiger_sources(settings))
    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    return _refresh_aux_data(
        db, "dragon_tiger", sources, settings,
        fetcher=lambda source, _settings: [
            row
            for row in _fetch_dragon_tiger(source, _settings, trade_date=target_trade_date)
            if _normalize_trade_date(row.get("trade_date")) == target_trade_date
        ],
        normalized_persister=_persist_normalized_dragon_tiger,
    )


def refresh_capital_flows_for_codes(db: Session, codes: list[str], trade_date: str | None = None) -> dict[str, dict[str, Any]]:
    """刷新资金流向数据（批量模式优先，fallback 到逐股票）。"""
    settings = get_settings()
    if not settings.enable_capital_flow_fetch:
        return {}
    sources = enabled_sources(db, "capital_flow", _capital_flow_sources(settings))
    if not sources:
        return {}
    source = sources[0]
    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    result: dict[str, dict[str, Any]] = {}

    # 批量模式：一次 API 拉全市场，过滤目标 codes（~12x 快于逐股票）
    batch_data = _fetch_capital_flow_batch(source, codes, settings, trade_date=target_trade_date)
    if batch_data:
        for code, data in batch_data.items():
            data["trade_date"] = _normalize_trade_date(data.get("trade_date"))
            if target_trade_date and data["trade_date"] != target_trade_date:
                continue
            _persist_normalized_capital_flow(db, data)
            result[code] = data
        db.commit()
        return result

    # Fallback：逐股票调用
    for code in codes:
        try:
            data = _fetch_capital_flow(source, code, settings, trade_date=target_trade_date)
            if data:
                data["trade_date"] = _normalize_trade_date(data.get("trade_date"))
                if target_trade_date and data["trade_date"] != target_trade_date:
                    continue
                _persist_normalized_capital_flow(db, data)
                result[code] = data
        except Exception:
            pass
    db.commit()
    return result


class NewsRefreshResult(dict[str, list[dict[str, Any]]]):
    def __init__(self, rows: dict[str, list[dict[str, Any]]], checked_codes: list[str]) -> None:
        super().__init__(rows)
        self.checked_codes = checked_codes


def refresh_news_for_codes(db: Session, codes: list[str], trade_date: str | None = None) -> NewsRefreshResult:
    """按股刷新新闻公告数据。"""
    settings = get_settings()
    if not settings.enable_news_fetch:
        return NewsRefreshResult({}, [])
    sources = enabled_sources(db, "news", _news_sources(settings))
    if not sources:
        return NewsRefreshResult({}, [])
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    checked_codes: list[str] = []
    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    ttl_days = _date_window_days(settings.news_cache_ttl_seconds, minimum_days=7)
    latest_allowed = date.fromisoformat(target_trade_date)
    earliest_allowed = latest_allowed - timedelta(days=ttl_days)
    for code in codes:
        code_checked = False
        seen_articles: set[tuple[str, str]] = set()
        try:
            normalized_items = []
            for source in sources:
                try:
                    items = _fetch_news(source, code, settings, trade_date=target_trade_date)
                    code_checked = True
                except Exception:
                    continue
                for item in items:
                    announce_date = _normalize_trade_date(item.get("announce_date"))
                    if target_trade_date:
                        if not announce_date:
                            continue
                        announce_day = date.fromisoformat(announce_date)
                        if announce_day > latest_allowed or announce_day < earliest_allowed:
                            continue
                    article_key = (
                        str(item.get("source") or source or "unknown"),
                        str(item.get("article_id") or ""),
                    )
                    if not article_key[1] or article_key in seen_articles:
                        continue
                    seen_articles.add(article_key)
                    normalized_items.append({**item, "source": article_key[0], "announce_date": announce_date})
            if code_checked:
                checked_codes.append(code)
            if not normalized_items:
                continue
            _persist_normalized_news(db, normalized_items)
            result[code] = normalized_items
        except Exception:
            pass
    db.commit()
    return NewsRefreshResult(dict(result), checked_codes)


# ── 辅助数据池：通用刷新模式 ───────────────────────────────────────────

def _refresh_aux_data(
    db: Session,
    source_type: str,
    sources: list[str],
    settings: Any,
    fetcher: Any,
    normalized_persister: Any,
) -> list[dict[str, Any]]:
    if not sources:
        return []
    all_rows: list[dict[str, Any]] = []
    for source in sources:
        start = time.perf_counter()
        try:
            rows = fetcher(source, settings)
            latency_ms = (time.perf_counter() - start) * 1000
            if not rows:
                _record_source_health(db, source, source_type, "empty", latency_ms=latency_ms)
                continue
            _record_source_health(db, source, source_type, "success", latency_ms=latency_ms)
            if all_rows:
                all_rows.extend(rows)
            else:
                all_rows = rows
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000
            _record_source_health(db, source, source_type, "failure", error=str(exc), latency_ms=latency_ms)
    if all_rows:
        normalized_persister(db, all_rows)
        db.commit()
    return all_rows


# ── 辅助数据池：标准化数据持久化（走 AuxiliaryDataset 描述符 upsert-by-key） ──

def _persist_normalized_limit_up_board(db: Session, rows: list[dict[str, Any]]) -> None:
    _upsert_by_key(db, AUXILIARY_DATASETS["limit_up_board"], rows)


def _persist_normalized_concept_sector(db: Session, rows: list[dict[str, Any]]) -> None:
    _upsert_by_key(db, AUXILIARY_DATASETS["concept_sector"], rows)


def _persist_concept_sector_stocks(db: Session, rows: list[dict[str, Any]]) -> None:
    _upsert_by_key(db, AUXILIARY_DATASETS["concept_sector_stock"], rows)


def _persist_normalized_capital_flow(db: Session, data: dict[str, Any]) -> None:
    code = str(data.get("code") or "")
    trade_date = _normalize_trade_date(data.get("trade_date"))
    if not code or not trade_date:
        return
    _upsert_by_key(db, AUXILIARY_DATASETS["capital_flow"], [{**data, "trade_date": trade_date}])


def _persist_normalized_dragon_tiger(db: Session, rows: list[dict[str, Any]]) -> None:
    normalized_rows = []
    for row in rows:
        code = str(row.get("code") or "")
        trade_date = _normalize_trade_date(row.get("trade_date"))
        if not code or not trade_date:
            continue
        normalized_rows.append({**row, "trade_date": trade_date})
    _upsert_by_key(db, AUXILIARY_DATASETS["dragon_tiger"], normalized_rows)


def _persist_normalized_news(db: Session, rows: list[dict[str, Any]]) -> None:
    """新闻公告持久化：key 为 (source, article_id) 二元组，且写入失败需回滚（去重语义
    与其余数据集不同，形状不适用通用 `_upsert_by_key`，保留原实现）。"""
    seen_batch: set[tuple] = set()
    for row in rows:
        article_id = str(row.get("article_id") or "")
        source = str(row.get("source") or "unknown")
        if not article_id:
            continue
        key = (source, article_id)
        if key in seen_batch:
            continue
        seen_batch.add(key)
        row_with_source = {**row, "source": source, "announce_date": _normalize_trade_date(row.get("announce_date"))}
        existing = db.scalar(
            select(NewsAnnouncement).where(NewsAnnouncement.source == source, NewsAnnouncement.article_id == article_id)
        )
        if existing is None:
            try:
                db.add(NewsAnnouncement(**{k: v for k, v in row_with_source.items() if hasattr(NewsAnnouncement, k)}))
                db.flush()
            except Exception:
                db.rollback()
        else:
            for key_name, value in row_with_source.items():
                if hasattr(existing, key_name) and key_name not in {"id", "created_at"}:
                    setattr(existing, key_name, value)
            existing.created_at = datetime.now(timezone.utc).replace(tzinfo=None)


# ── 辅助数据池：缓存读取 ───────────────────────────────────────────────

def _load_cached_limit_up_board(
    db: Session,
    trade_date: str | None = None,
    max_age_seconds: int | None = None,
) -> list[dict[str, Any]]:
    target_trade_date = _normalize_trade_date(trade_date)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=max_age_seconds) if max_age_seconds else None
    stmt = select(LimitUpBoard).order_by(LimitUpBoard.limit_times.desc())
    # limit_type='U' 涨停 / 'D' 跌停；涨停策略只关心涨停，排除跌停股被误标 is_limit_up
    stmt = stmt.where(LimitUpBoard.limit_type == "U")
    if target_trade_date:
        stmt = stmt.where(LimitUpBoard.trade_date == target_trade_date)
    if cutoff is not None:
        stmt = stmt.where(LimitUpBoard.created_at >= cutoff)
    rows = db.scalars(stmt).all()
    return [
        {c.name: getattr(r, c.name) for c in r.__table__.columns}
        for r in rows
    ]


def _load_cached_broken_limit_up(
    db: Session,
    trade_date: str | None = None,
    max_age_seconds: int | None = None,
) -> dict[str, dict[str, Any]]:
    """加载炸板数据（limit_type='Z'）。返回 {code: row} 字典。

    炸板=曾涨停后打开，炸板率高=市场情绪转弱，用于短线情绪温度计。
    """
    target_trade_date = _normalize_trade_date(trade_date)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=max_age_seconds) if max_age_seconds else None
    stmt = select(LimitUpBoard).where(LimitUpBoard.limit_type == "Z")
    if target_trade_date:
        stmt = stmt.where(LimitUpBoard.trade_date == target_trade_date)
    if cutoff is not None:
        stmt = stmt.where(LimitUpBoard.created_at >= cutoff)
    rows = db.scalars(stmt).all()
    return {
        str(r.code): {c.name: getattr(r, c.name) for c in r.__table__.columns}
        for r in rows
    }


def _load_cached_concept_sectors(
    db: Session,
    trade_date: str | None = None,
    max_age_seconds: int | None = None,
) -> list[dict[str, Any]]:
    target_trade_date = _normalize_trade_date(trade_date)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=max_age_seconds) if max_age_seconds else None
    stmt = select(ConceptSector).order_by(ConceptSector.heat_rank)
    if target_trade_date:
        stmt = stmt.where(ConceptSector.trade_date == target_trade_date)
    if cutoff is not None:
        stmt = stmt.where(ConceptSector.created_at >= cutoff)
    rows = db.scalars(stmt).all()
    return [
        {c.name: getattr(r, c.name) for c in r.__table__.columns}
        for r in rows
    ]


def _load_cached_capital_flows(
    db: Session,
    codes: list[str],
    trade_date: str | None = None,
    max_age_seconds: int | None = None,
) -> dict[str, dict[str, Any]]:
    target_trade_date = _normalize_trade_date(trade_date)
    stmt = select(CapitalFlow).where(CapitalFlow.code.in_(codes))
    if target_trade_date:
        stmt = stmt.where(CapitalFlow.trade_date == target_trade_date)
    if max_age_seconds:
        stmt = stmt.where(CapitalFlow.created_at >= datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=max_age_seconds))
    rows = db.scalars(stmt).all()
    return {
        r.code: {c.name: getattr(r, c.name) for c in r.__table__.columns}
        for r in rows
    }


def _load_cached_dragon_tigers(
    db: Session,
    codes: list[str],
    trade_date: str | None = None,
    max_age_seconds: int | None = None,
) -> dict[str, dict[str, Any]]:
    target_trade_date = _normalize_trade_date(trade_date)
    stmt = select(DragonTiger).where(DragonTiger.code.in_(codes))
    if target_trade_date:
        stmt = stmt.where(DragonTiger.trade_date == target_trade_date)
    if max_age_seconds:
        stmt = stmt.where(DragonTiger.created_at >= datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=max_age_seconds))
    rows = db.scalars(stmt).all()
    return {
        r.code: {c.name: getattr(r, c.name) for c in r.__table__.columns}
        for r in rows
    }


def _load_cached_news(
    db: Session,
    codes: list[str],
    trade_date: str | None = None,
    max_age_seconds: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    target_trade_date = _normalize_trade_date(trade_date)
    stmt = select(NewsAnnouncement).where(NewsAnnouncement.code.in_(codes))
    if target_trade_date:
        ttl_days = _date_window_days(max_age_seconds, minimum_days=2)
        latest_allowed = date.fromisoformat(target_trade_date)
        earliest_allowed = latest_allowed - timedelta(days=ttl_days)
        stmt = stmt.where(
            NewsAnnouncement.announce_date <= latest_allowed.isoformat(),
            NewsAnnouncement.announce_date >= earliest_allowed.isoformat(),
        )
    if max_age_seconds:
        stmt = stmt.where(NewsAnnouncement.created_at >= datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=max_age_seconds))
    rows = db.scalars(stmt).all()
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        result[r.code].append({c.name: getattr(r, c.name) for c in r.__table__.columns})
    for code, items in result.items():
        items.sort(key=lambda item: str(item.get("announce_date") or ""), reverse=True)
    return dict(result)


def _load_concept_sectors_for_stocks(
    db: Session,
    codes: list[str],
    trade_date: str | None = None,
    max_age_seconds: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """根据成分股表返回每只股票所属的概念板块。"""
    target_trade_date = _normalize_trade_date(trade_date)
    stmt = select(ConceptSectorStock).where(ConceptSectorStock.code.in_(codes))
    if target_trade_date:
        stmt = stmt.where(ConceptSectorStock.trade_date == target_trade_date)
    cutoff = (
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=max_age_seconds)
        if max_age_seconds
        else None
    )
    if cutoff is not None:
        stmt = stmt.where(ConceptSectorStock.created_at >= cutoff)
    rows = db.scalars(stmt).all()
    sector_codes = {r.sector_code for r in rows}
    sectors = {
        s.sector_code: {c.name: getattr(s, c.name) for c in s.__table__.columns}
        for s in db.scalars(
            select(ConceptSector).where(
                ConceptSector.sector_code.in_(sector_codes),
                *((
                    ConceptSector.trade_date == target_trade_date,
                ) if target_trade_date else ()),
                *((
                    ConceptSector.created_at >= cutoff,
                ) if cutoff is not None else ()),
            )
        ).all()
    }
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        sector = sectors.get(r.sector_code)
        if sector:
            result[r.code].append(sector)
    return dict(result)


# ── 辅助数据：批量加载入口（供feature_builder使用） ────────────────────

def _load_auction_for_stocks(db: Session, codes: list[str], trade_date: str | None = None) -> dict[str, dict[str, Any]]:
    """加载当日集合竞价数据。"""
    from app.models.tables import AuctionData
    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    rows = db.scalars(
        select(AuctionData).where(
            AuctionData.code.in_(codes), AuctionData.trade_date == target_trade_date
        )
    ).all()
    return {
        r.code: {c.name: getattr(r, c.name) for c in r.__table__.columns}
        for r in rows
    }


def _load_pledge_for_stocks(db: Session, codes: list[str]) -> dict[str, dict[str, Any]]:
    """加载最新股权质押统计。"""
    from app.models.tables import PledgeStat
    from sqlalchemy import and_
    subq = (
        select(PledgeStat.code, func.max(PledgeStat.end_date).label("max_ed"))
        .where(PledgeStat.code.in_(codes))
        .group_by(PledgeStat.code)
        .subquery()
    )
    rows = db.scalars(
        select(PledgeStat).join(
            subq, and_(PledgeStat.code == subq.c.code, PledgeStat.end_date == subq.c.max_ed)
        )
    ).all()
    return {
        r.code: {c.name: getattr(r, c.name) for c in r.__table__.columns}
        for r in rows
    }


def _load_broker_recs_for_stocks(db: Session, codes: list[str], trade_date: str | None = None) -> dict[str, int]:
    """加载本月券商金股推荐次数。"""
    from app.models.tables import BrokerRecommend
    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    month = target_trade_date[:7].replace("-", "")
    rows = db.execute(
        select(BrokerRecommend.code, func.count().label("cnt"))
        .where(BrokerRecommend.code.in_(codes), BrokerRecommend.month == month)
        .group_by(BrokerRecommend.code)
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _load_share_float_upcoming(
    db: Session,
    codes: list[str],
    trade_date: str | None = None,
    days: int = 30,
) -> dict[str, list[dict[str, Any]]]:
    """加载即将到来的限售解禁（未来N天）。"""
    from app.models.tables import ShareFloat
    today = date.fromisoformat(_resolve_snapshot_trade_date(db, trade_date))
    cutoff = (today + timedelta(days=days)).isoformat()
    rows = db.scalars(
        select(ShareFloat).where(
            ShareFloat.code.in_(codes),
            ShareFloat.float_date >= today.isoformat(),
            ShareFloat.float_date <= cutoff,
        )
    ).all()
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        result[r.code].append({c.name: getattr(r, c.name) for c in r.__table__.columns})
    return dict(result)


def _persist_fina_indicator(db: Session, rows: list[dict[str, Any]]) -> None:
    """持久化财务指标（原子 upsert by code+end_date）。

    用 mysql_insert.on_duplicate_key_update 替代 check-then-insert：
    并发 run 时两个请求同时检查"不存在"→ 同时 INSERT → 唯一键冲突 (1062)
    导致整个推荐管线 500。原子 upsert 由 MySQL 保证 by code+end_date 唯一性。
    形状与 `_upsert_by_key` 的 check-then-insert 不同（原子性更强），不套用描述符。
    """
    if not rows:
        return
    from sqlalchemy.dialects.mysql import insert as mysql_insert
    from app.models.tables import FinaIndicator

    columns = set(FinaIndicator.__mapper__.columns.keys()) - {"id", "updated_at"}
    chunk: list[dict[str, Any]] = []
    # 收集本次 INSERT 实际出现的列（不含 code/end_date 唯一键）
    update_col_names: set[str] = set()
    for row in rows:
        code = str(row.get("code") or "").strip()
        end_date = str(row.get("end_date") or "").strip()
        if len(code) != 6 or not end_date:
            continue
        payload = {k: v for k, v in row.items() if k in columns}
        if not payload:
            continue
        chunk.append(payload)
        update_col_names.update(k for k in payload if k not in {"code", "end_date"})
    if not chunk:
        return
    stmt = mysql_insert(FinaIndicator).values(chunk)
    # 只 UPDATE 本次 INSERT 实际出现的列（避免引用 new.<missing_col>）
    update_cols = {c: stmt.inserted[c] for c in update_col_names}
    db.execute(stmt.on_duplicate_key_update(**update_cols))
    db.commit()


def _load_fina_indicator_for_stocks(db: Session, codes: list[str], trade_date: str | None = None) -> dict[str, dict[str, Any]]:
    """加载最新一期已披露财务指标；本地缺失时按需从 Tushare 拉取并入库。"""
    if not codes:
        return {}
    from app.models.tables import FinaIndicator
    from sqlalchemy import and_

    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    target_ann_date = target_trade_date.replace("-", "") if target_trade_date else ""

    def _query_latest() -> dict[str, dict[str, Any]]:
        subq_stmt = (
            select(FinaIndicator.code, func.max(FinaIndicator.end_date).label("max_ed"))
            .where(FinaIndicator.code.in_(codes))
        )
        if target_ann_date:
            subq_stmt = subq_stmt.where(FinaIndicator.ann_date <= target_ann_date)
        subq = subq_stmt.group_by(FinaIndicator.code).subquery()
        rows = db.scalars(
            select(FinaIndicator).join(
                subq, and_(
                    FinaIndicator.code == subq.c.code,
                    FinaIndicator.end_date == subq.c.max_ed,
                )
            )
        ).all()
        return {
            r.code: {
                "roe": r.roe,
                "gross_margin": r.grossprofit_margin,        # feature_builder 读 gross_margin
                "net_profit_yoy": r.netprofit_yoy,           # feature_builder 读 net_profit_yoy
                "revenue_yoy": r.or_yoy,                     # feature_builder 读 revenue_yoy
                "netprofit_margin": r.netprofit_margin,
                "eps": r.eps,
                "bps": r.bps,
                "end_date": r.end_date,
                "ann_date": r.ann_date,
            }
            for r in rows
        }

    result = _query_latest()
    missing_codes = [code for code in codes if code not in result]
    if missing_codes:
        from app.services.tushare_client import fetch_fina_indicator

        fetched: list[dict[str, Any]] = []
        for code in missing_codes:
            fetched.extend(fetch_fina_indicator(code, trade_date=target_trade_date, limit=4))
        if fetched:
            _persist_fina_indicator(db, fetched)
            result = _query_latest()
    return result


def load_auxiliary_for_stocks(db: Session, codes: list[str], trade_date: str | None = None) -> dict[str, Any]:
    """批量加载所有辅助数据，返回按code索引的字典。"""
    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    limit_up = {
        r["code"]: r
        for r in _load_cached_limit_up_board(
            db,
            trade_date=target_trade_date,
        )
    }
    concept_sectors = _load_concept_sectors_for_stocks(
        db,
        codes,
        trade_date=target_trade_date,
    )
    capital_flows = _load_cached_capital_flows(
        db,
        codes,
        trade_date=target_trade_date,
    )
    dragon_tigers = _load_cached_dragon_tigers(
        db,
        codes,
        trade_date=target_trade_date,
    )
    news = _load_cached_news(
        db,
        codes,
        trade_date=target_trade_date,
    )
    return {
        "limit_up": limit_up,
        "concept_sectors": concept_sectors,
        "capital_flows": capital_flows,
        "dragon_tigers": dragon_tigers,
        "news": news,
        "auctions": _load_auction_for_stocks(db, codes, trade_date=target_trade_date),
        "pledges": _load_pledge_for_stocks(db, codes),
        "broker_recs": _load_broker_recs_for_stocks(db, codes, trade_date=target_trade_date),
        "share_floats_upcoming": _load_share_float_upcoming(db, codes, trade_date=target_trade_date),
        "margin_details": _load_margin_detail_for_stocks(db, codes, trade_date=target_trade_date),
        "block_trades": _load_block_trade_for_stocks(db, codes, trade_date=target_trade_date),
        "hsgt_top10": _load_hsgt_top10_for_stocks(db, codes, trade_date=target_trade_date),
        "hsgt_flow": _load_hsgt_flow(db, trade_date=target_trade_date),
        "finas": _load_fina_indicator_for_stocks(db, codes, trade_date=target_trade_date),
        "broken_limit_up": _load_cached_broken_limit_up(db, trade_date=target_trade_date),
        "holder_numbers": _load_holder_number_for_stocks(db, codes, trade_date=target_trade_date),
        "holder_trades": _load_holder_trade_for_stocks(db, codes, trade_date=target_trade_date),
        "surveys": _load_survey_for_stocks(db, codes, trade_date=target_trade_date),
        "coverage": _auxiliary_coverage_for_stocks(
            db,
            codes,
            target_trade_date,
            concept_sectors=concept_sectors,
            capital_flows=capital_flows,
            dragon_tigers=dragon_tigers,
            news=news,
        ),
    }


def inject_limit_up_flags(db: Session, stocks: list[dict[str, Any]], trade_date: str | None = None) -> int:
    """prefilter 前给 stocks 注入涨停板基础字段（is_limit_up / limit_up_consecutive_days 等）。

    涨停是已发生的客观事实，本地 limit_up_board 表已持久化。limit-up-reseal-relay 等
    策略的 universe 依赖这些字段，但 feature_builder 在 prefilter 之后才运行，导致
    prefilter 阶段 is_limit_up 全部缺失 → 策略 universe 永远 0 命中。本函数轻量注入
    （单表查询，~133 行），让 prefilter 能正确识别涨停股。
    返回命中的涨停股数量。
    """
    if not stocks:
        return 0
    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    rows = _load_cached_limit_up_board(db, trade_date=target_trade_date)
    lu_map: dict[str, dict[str, Any]] = {r["code"]: r for r in rows}
    hit = 0
    for stock in stocks:
        code = str(stock.get("code") or "")
        lu = lu_map.get(code)
        if lu:
            hit += 1
            stock["is_limit_up"] = True
            stock["limit_up_consecutive_days"] = int(lu.get("limit_times") or 1)
            stock["limit_up_open_times"] = int(lu.get("open_times") or 0)
            stock["limit_up_fd_amount_yi"] = float(lu.get("fd_amount_yi") or 0.0)
            stock["limit_up_limit_amount_yi"] = float(lu.get("limit_amount_yi") or 0.0)
            stock["limit_up_turnover_ratio"] = float(lu.get("turnover_ratio") or 0.0)
            # 涨停股必然 limit_up_days20>=1；twenty_cm_risk_gate 默认 pass（feature_builder 会重算覆盖）
            stock.setdefault("limit_up_days20", 1)
            stock.setdefault("twenty_cm_risk_gate", "pass")
        else:
            stock.setdefault("is_limit_up", False)
            stock.setdefault("limit_up_consecutive_days", 0)
            stock.setdefault("limit_up_open_times", 0)
        # 所有股票（含非涨停）都补 twenty_cm_risk_gate 默认值，否则 prefilter 阶段该字段
        # 缺失（feature_builder 在 prefilter 之后才重算），strict_missing 下
        # `twenty_cm_risk_gate != 'block'` 会拒绝全部非涨停股（overnight/sector 等策略死锁）。
        # not_applicable 语义=未做 20cm 风险检测=不阻断，与 shortline_factors 一致。
        stock.setdefault("twenty_cm_risk_gate", "not_applicable")
    return hit


def inject_dragon_tiger_flags(db: Session, stocks: list[dict[str, Any]], trade_date: str | None = None) -> int:
    """prefilter 前给 stocks 注入龙虎榜基础字段（dragon_tiger_is_on_list / net_amount_yi / net_buy_ratio 等）。

    龙虎榜是已发生的客观事实，本地 dragon_tiger 表已持久化。dragon-tiger-smart-money 等策略的
    universe 依赖 dragon_tiger_is_on_list，但 feature_builder 在 prefilter 之后才运行，导致 prefilter
    阶段 dragon_tiger 字段全部缺失 → 策略 universe 永远 0 命中。本函数轻量注入（单表按交易日查询），
    让 prefilter 能正确识别龙虎榜股。net_buy_ratio 计算与 feature_builder 一致（净买占成交额比例），
    避免重复计算与语义漂移。返回命中龙虎榜的股票数。
    """
    if not stocks:
        return 0
    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    rows = db.scalars(select(DragonTiger).where(DragonTiger.trade_date == target_trade_date)).all()
    dt_map: dict[str, dict[str, Any]] = {r.code: {c.name: getattr(r, c.name) for c in r.__table__.columns} for r in rows}
    hit = 0
    for stock in stocks:
        code = str(stock.get("code") or "")
        dt = dt_map.get(code)
        if dt:
            hit += 1
            net_amount_yi = float(dt.get("net_amount_yi") or 0.0)
            amount_yi = float(stock.get("amount_yi") or 0.0)
            net_buy_ratio = round(min(1.0, max(-1.0, net_amount_yi / amount_yi)), 4) if amount_yi else 0.0
            stock["dragon_tiger_is_on_list"] = True
            stock["dragon_tiger_net_amount_yi"] = net_amount_yi
            # 修复沉睡因子：institution_net_buy_yi 此前从未赋值 → inst_buy_ratio 恒为 0。
            # 龙虎榜席位=机构+游资=聪明钱，用净买额作为聪明钱净买代理。
            stock["dragon_tiger_institution_net_buy_yi"] = net_amount_yi
            stock["dragon_tiger_net_buy_ratio"] = net_buy_ratio
        else:
            stock.setdefault("dragon_tiger_is_on_list", False)
    return hit


def _auxiliary_coverage_for_stocks(
    db: Session,
    codes: list[str],
    target_trade_date: str,
    *,
    concept_sectors: dict[str, list[dict[str, Any]]],
    capital_flows: dict[str, dict[str, Any]],
    dragon_tigers: dict[str, dict[str, Any]],
    news: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """记录辅助数据覆盖语义，区分"已查为空"和"未覆盖"。"""
    dt_stmt = select(func.count()).select_from(DragonTiger).where(DragonTiger.trade_date == target_trade_date)
    dragon_tiger_count = int(db.scalar(dt_stmt) or 0)

    concept_stmt = select(func.count()).select_from(ConceptSector).where(ConceptSector.trade_date == target_trade_date)
    member_stmt = select(func.count()).select_from(ConceptSectorStock).where(ConceptSectorStock.trade_date == target_trade_date)

    return {
        "trade_date": target_trade_date,
        "dragon_tiger_trade_date_covered": dragon_tiger_count > 0,
        "dragon_tiger_trade_date_count": dragon_tiger_count,
        "dragon_tiger_codes": sorted(dragon_tigers),
        "concept_sector_trade_date_covered": int(db.scalar(concept_stmt) or 0) > 0,
        "concept_member_trade_date_covered": int(db.scalar(member_stmt) or 0) > 0,
        "concept_sector_codes": sorted(concept_sectors),
        "capital_flow_checked_codes": sorted(capital_flows),
        "news_codes_with_items": sorted(news),
        "requested_codes": sorted({str(code) for code in codes if code}),
    }


# ═════════════════════════════════════════════════════════════════════════════
# 融资融券明细（全市场批量同步）
# ═════════════════════════════════════════════════════════════════════════════


def refresh_margin_detail(db: Session, trade_date: str | None = None) -> int:
    """批量同步全市场融资融券明细。返回入库条数。"""
    from app.services.tushare_client import fetch_margin_detail_batch
    rows = fetch_margin_detail_batch(trade_date=trade_date)
    if not rows:
        return 0
    _persist_margin_detail(db, rows)
    return len(rows)


def _persist_margin_detail(db: Session, rows: list[dict[str, Any]]) -> None:
    """持久化融资融券明细（upsert by code+trade_date，走 AuxiliaryDataset 描述符）。"""
    _upsert_by_key(db, AUXILIARY_DATASETS["margin_detail"], rows)
    db.commit()


def _load_margin_detail_for_stocks(
    db: Session, codes: list[str], trade_date: str | None = None
) -> dict[str, dict[str, Any]]:
    """加载指定股票的融资融券明细。"""
    stmt = select(MarginDetail).where(
        MarginDetail.code.in_(codes),
        MarginDetail.trade_date == trade_date,
    )
    rows = db.scalars(stmt).all()
    return {r.code: {"rzye": r.rzye, "rqye": r.rqye, "rzmre": r.rzmre, "rzche": r.rzche, "rzrqye": r.rzrqye} for r in rows}


# ═════════════════════════════════════════════════════════════════════════════
# 大宗交易
# ═════════════════════════════════════════════════════════════════════════════


def refresh_block_trade(db: Session, trade_date: str | None = None) -> int:
    """同步大宗交易数据。返回入库条数。"""
    from app.services.tushare_client import fetch_block_trade
    rows = fetch_block_trade(trade_date=trade_date)
    if not rows:
        return 0
    _persist_block_trade(db, rows)
    return len(rows)


def _persist_block_trade(db: Session, rows: list[dict[str, Any]]) -> None:
    """持久化大宗交易（先删后插，避免重复）。非 upsert-by-key 语义，不套用描述符。"""
    if not rows:
        return
    trade_dates = {r["trade_date"] for r in rows}
    for td in trade_dates:
        codes = {r["code"] for r in rows if r["trade_date"] == td}
        db.execute(BlockTrade.__table__.delete().where(
            BlockTrade.trade_date == td,
            BlockTrade.code.in_(codes),
        ))
    for row in rows:
        db.add(BlockTrade(**{k: v for k, v in row.items() if k in BlockTrade.__mapper__.columns.keys()}))
    db.commit()


def _load_block_trade_for_stocks(
    db: Session, codes: list[str], trade_date: str | None = None
) -> dict[str, list[dict[str, Any]]]:
    """加载指定股票的大宗交易。"""
    stmt = select(BlockTrade).where(
        BlockTrade.code.in_(codes),
        BlockTrade.trade_date == trade_date,
    )
    rows = db.scalars(stmt).all()
    result: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        result.setdefault(r.code, []).append({
            "price": r.price,
            "vol": r.vol,
            "amount_yi": r.amount_yi,
            "buyer": r.buyer,
            "seller": r.seller,
        })
    return result


def _load_holder_number_for_stocks(
    db: Session, codes: list[str], trade_date: str | None = None
) -> dict[str, dict[str, Any]]:
    """加载股东人数（筹码集中度代理）。取最近两期对比变化率。"""
    if not codes:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for code in codes:
        rows = db.scalars(
            select(HolderNumber)
            .where(HolderNumber.code == code)
            .order_by(HolderNumber.end_date.desc())
            .limit(2)
        ).all()
        if not rows:
            continue
        latest = rows[0]
        holder_num = latest.holder_num or 0
        prev_num = rows[1].holder_num if len(rows) > 1 else 0
        # 股东人数减少 = 筹码集中 = 看多信号
        holder_num_change_pct = (
            round((holder_num - prev_num) / prev_num * 100, 2)
            if prev_num > 0 else 0.0
        )
        result[code] = {
            "holder_num": holder_num,
            "holder_num_end_date": latest.end_date,
            "holder_num_prev": prev_num,
            "holder_num_change_pct": holder_num_change_pct,
            "holder_num_decreasing": holder_num_change_pct < -3,  # 减少>3%为筹码集中信号
        }
    return result


def _load_holder_trade_for_stocks(
    db: Session, codes: list[str], trade_date: str | None = None
) -> dict[str, dict[str, Any]]:
    """加载股东增减持数据（近30天）。trade_date 为 YYYY-MM-DD 或空。"""
    if not codes:
        return {}
    # trade_date 可能是空字符串（snapshot 表为空时），用今天兜底，避免 fromisoformat("") 崩溃
    ref_date: date
    if trade_date and len(trade_date) >= 10:
        try:
            ref_date = date.fromisoformat(trade_date[:10])
        except ValueError:
            ref_date = date.today()
    else:
        ref_date = date.today()
    cutoff = (ref_date - timedelta(days=30)).isoformat()
    stmt = select(HolderTrade).where(
        HolderTrade.code.in_(codes),
        HolderTrade.ann_date >= cutoff,
    )
    rows = db.scalars(stmt).all()
    # 按股票聚合
    by_code: dict[str, list] = {}
    for r in rows:
        by_code.setdefault(r.code, []).append(r)
    result: dict[str, dict[str, Any]] = {}
    for code, recs in by_code.items():
        increase_count = sum(1 for r in recs if r.in_de == "IN")
        decrease_count = sum(1 for r in recs if r.in_de == "DE")
        total_change_ratio = sum(r.change_ratio or 0 for r in recs)
        result[code] = {
            "holder_trade_count": len(recs),
            "holder_trade_increase_count": increase_count,
            "holder_trade_decrease_count": decrease_count,
            "holder_trade_net_ratio": round(total_change_ratio, 4),
            "holder_trade_latest_ann": max((r.ann_date or "") for r in recs),
        }
    return result


def _load_survey_for_stocks(
    db: Session, codes: list[str], trade_date: str | None = None
) -> dict[str, dict[str, Any]]:
    """加载机构调研数据（近30天）。trade_date 为 YYYY-MM-DD 或空。"""
    if not codes:
        return {}
    # 同 _load_holder_trade_for_stocks：空 trade_date 用今天兜底
    ref_date: date
    if trade_date and len(trade_date) >= 10:
        try:
            ref_date = date.fromisoformat(trade_date[:10])
        except ValueError:
            ref_date = date.today()
    else:
        ref_date = date.today()
    cutoff = (ref_date - timedelta(days=30)).isoformat()
    stmt = select(InstitutionalSurvey).where(
        InstitutionalSurvey.code.in_(codes),
        InstitutionalSurvey.surv_date >= cutoff,
    )
    rows = db.scalars(stmt).all()
    by_code: dict[str, list] = {}
    for r in rows:
        by_code.setdefault(r.code, []).append(r)
    result: dict[str, dict[str, Any]] = {}
    for code, recs in by_code.items():
        total_visitors = sum(r.fund_visitors or 0 for r in recs)
        result[code] = {
            "survey_count": len(recs),
            "survey_fund_visitors": total_visitors,
            "survey_latest_date": max((r.surv_date or "") for r in recs),
            "survey_org_types": list({r.org_type or "" for r in recs if r.org_type})[:5],
        }
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 北向资金
# ═════════════════════════════════════════════════════════════════════════════


def refresh_hsgt_flow(db: Session, trade_date: str | None = None) -> dict[str, Any] | None:
    """同步沪深港通资金流向。返回入库数据。"""
    from app.services.tushare_client import fetch_hsgt_flow
    data = fetch_hsgt_flow(trade_date=trade_date)
    if not data:
        return None
    _persist_hsgt_flow(db, data)
    return data


def _persist_hsgt_flow(db: Session, data: dict[str, Any]) -> None:
    """持久化北向资金流向（upsert by trade_date，走 AuxiliaryDataset 描述符）。"""
    _upsert_by_key(db, AUXILIARY_DATASETS["hsgt_flow"], [data])
    db.commit()


def refresh_hsgt_top10(db: Session, trade_date: str | None = None) -> int:
    """同步北向持股TOP10。返回入库条数。"""
    from app.services.tushare_client import fetch_hsgt_top10
    rows = fetch_hsgt_top10(trade_date=trade_date)
    if not rows:
        return 0
    _persist_hsgt_top10(db, rows)
    return len(rows)


def _persist_hsgt_top10(db: Session, rows: list[dict[str, Any]]) -> None:
    """持久化北向持股TOP10。

    **不套用 `_upsert_by_key` 描述符**：源数据字段名是 `change`，但模型列名是
    `change_pct`（字段名不一致），且原实现里这个映射只存在于"更新已存在记录"分支
    （`existing.change_pct = row.get("change")`），插入新记录分支用
    `k in HsgtTop10.__mapper__.columns.keys()` 过滤，`"change"` 不是列名会被
    静默丢弃——插入与更新路径行为不对称（原实现的既有行为，不是本次重构要修的
    bug）。通用 upsert 描述符假设"row 字段名 == 模型列名"，套用会静默改变这个
    不对称行为，因此保留原始逐字段实现。
    """
    for row in rows:
        existing = db.scalar(
            select(HsgtTop10).where(
                HsgtTop10.code == row["code"],
                HsgtTop10.trade_date == row["trade_date"],
            )
        )
        if existing:
            existing.name = row.get("name")
            existing.close = row.get("close")
            existing.change_pct = row.get("change")
            existing.rank = row.get("rank")
            existing.amount = row.get("amount")
            existing.net_amount = row.get("net_amount")
            existing.buy = row.get("buy")
        else:
            db.add(HsgtTop10(**{k: v for k, v in row.items() if k in HsgtTop10.__mapper__.columns.keys()}))
    db.commit()


def _load_hsgt_top10_for_stocks(
    db: Session, codes: list[str], trade_date: str | None = None
) -> dict[str, dict[str, Any]]:
    """加载指定股票的北向持股数据。"""
    stmt = select(HsgtTop10).where(
        HsgtTop10.code.in_(codes),
        HsgtTop10.trade_date == trade_date,
    )
    rows = db.scalars(stmt).all()
    return {r.code: {"rank": r.rank, "amount": r.amount, "net_amount": r.net_amount, "buy": r.buy} for r in rows}


def _load_hsgt_flow(db: Session, trade_date: str | None = None) -> dict[str, Any] | None:
    """加载北向资金流向。"""
    row = db.scalar(select(HsgtFlow).where(HsgtFlow.trade_date == trade_date))
    if not row:
        return None
    return {"north_money": row.north_money, "south_money": row.south_money, "hgt": row.hgt, "sgt": row.sgt}
