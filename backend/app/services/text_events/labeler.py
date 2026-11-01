"""规则预筛之后才调用 LLM。相同 input_hash 直接复用，失败行下次重跑。

``ASHARE_TEXT_EVENT_LLM_ENABLED`` 默认关闭。关闭时程序性公告仍会落规则标签，
其余事件记为 skipped，不写失败行，打开开关后可以续跑。
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Callable, Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utcnow_naive
from app.models.market import TextEvent, TextEventLabel
from app.services.text_events.schema import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    keyword_columns,
    input_hash,
    parse_label_json,
    procedural_rule,
    user_prompt,
)

logger = logging.getLogger(__name__)


class Completer(Protocol):
    def complete(self, *, system: str, user: str) -> Any: ...


@dataclass(frozen=True)
class EventView:
    source: str
    event_id: str
    code: str
    title: str
    body: str
    publish_date: str
    available_at: str | None

    @classmethod
    def from_row(cls, row: TextEvent) -> "EventView":
        return cls(
            source=row.source,
            event_id=row.event_id,
            code=row.code or "",
            title=row.title or "",
            body=row.body or "",
            publish_date=row.publish_date,
            available_at=row.available_at,
        )


@dataclass
class LabelResult:
    source: str
    event_id: str
    status: str
    label: str | None = None
    category: str | None = None
    score: int | None = None
    confidence: float | None = None
    rationale: str | None = None
    keyword_label: str | None = None
    label_origin: str | None = None
    latency_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    model: str | None = None
    cached: bool = False
    error: str | None = None
    title: str = ""


def label_event(db: Session, event: EventView, *, settings: Any, completer: Completer | None = None) -> LabelResult:
    digest = input_hash(
        source=event.source, event_id=event.event_id, code=event.code,
        publish_date=event.publish_date, title=event.title, body=event.body,
    )
    keywords = keyword_columns(event.title, event.body)
    existing = _existing(db, event)
    rule = procedural_rule(event.title)
    if rule is not None:
        if _cache_hit(existing, digest, origin="rule"):
            return _from_row(existing, event, cached=True)
        row = _write(db, existing, event, digest, keywords, {
            "label": "中性", "category": rule.category, "score": 0, "confidence": 1.0,
            "rationale": rule.rationale, "model": "rule", "label_origin": "rule",
            "status": "ok", "prompt_tokens": 0, "completion_tokens": 0, "latency_ms": 0,
            "error_message": None,
        })
        return _from_row(row, event, cached=False)
    if _cache_hit(existing, digest, origin="llm"):
        return _from_row(existing, event, cached=True)
    if not bool(getattr(settings, "text_event_llm_enabled", False)):
        return LabelResult(
            source=event.source, event_id=event.event_id, status="skipped",
            keyword_label=keywords["keyword_label"], title=event.title,
            error="llm_disabled",
        )
    if completer is None:
        from app.services.text_events.llm_client import GatewayCompleter

        completer = GatewayCompleter(settings)
    prompt = user_prompt(
        source=event.source, code=event.code, publish_date=event.publish_date,
        title=event.title, body=event.body,
    )
    retries = max(0, int(getattr(settings, "text_event_llm_max_retries", 2) or 0))
    last_error = "empty"
    for _ in range(retries + 1):
        try:
            completion = completer.complete(system=SYSTEM_PROMPT, user=prompt)
        except Exception as exc:  # noqa: BLE001 - 记录后允许续跑
            last_error = str(exc)[:240]
            logger.warning("text event llm failed %s %s: %s", event.source, event.event_id, last_error)
            continue
        parsed = parse_label_json(getattr(completion, "content", "") or "")
        if parsed is None:
            last_error = "模型输出不符合 JSON 合同"
            continue
        row = _write(db, existing, event, digest, keywords, {
            **parsed,
            "model": getattr(completion, "model", None) or "llm",
            "label_origin": "llm",
            "status": "ok",
            "prompt_tokens": getattr(completion, "prompt_tokens", None),
            "completion_tokens": getattr(completion, "completion_tokens", None),
            "latency_ms": getattr(completion, "latency_ms", None),
            "error_message": None,
        })
        result = _from_row(row, event, cached=False)
        result.latency_ms = getattr(completion, "latency_ms", None)
        result.prompt_tokens = getattr(completion, "prompt_tokens", None)
        result.completion_tokens = getattr(completion, "completion_tokens", None)
        return result
    _write(db, existing, event, digest, keywords, {
        "label": keywords["keyword_label"], "category": "其他", "score": 0, "confidence": 0,
        "rationale": "模型输出失败，未采用", "model": "llm", "label_origin": "llm",
        "status": "failed", "error_message": last_error,
        "prompt_tokens": None, "completion_tokens": None, "latency_ms": None,
    })
    return LabelResult(
        source=event.source, event_id=event.event_id, status="failed",
        keyword_label=keywords["keyword_label"], label_origin="llm",
        error=last_error, title=event.title,
    )


def label_events(
    db: Session,
    events: list[EventView],
    *,
    settings: Any,
    completer: Completer | None = None,
) -> list[LabelResult]:
    return [label_event(db, event, settings=settings, completer=completer) for event in events]


def pending_events(db: Session, limit: int, *, scan_cap: int = 20000) -> list[EventView]:
    """挑还没有成功标签、或正文/规则已经对不上的事件。

    试跑时按来源分层，避免题材成分/龙虎榜席位把样本淹掉；
    优先公告、研报、调研、预告、回购、涨停原因等文本。
    """
    skip_sources = {"kpl_concept_cons"}
    prefer = [
        "announcement:eastmoney", "announcement:cninfo", "announcement:xiaoshi",
        "report_rc", "stk_surv", "forecast", "repurchase", "share_float",
        "stk_holdertrade", "kpl_list", "limit_list_ths", "ths_hot", "st",
        "dividend", "stk_managers", "stk_alert", "idx_anns", "eco_cal",
        "top_list", "stk_shock", "stk_high_shock", "cn_schedule", "top_inst",
    ]
    found: list[EventView] = []
    seen: set[tuple[str, str]] = set()

    def _take(rows: list) -> None:
        for row in rows:
            if row.source in skip_sources:
                continue
            key = (row.source, row.event_id)
            if key in seen:
                continue
            view = EventView.from_row(row)
            label = db.scalar(
                select(TextEventLabel).where(
                    TextEventLabel.prompt_version == PROMPT_VERSION,
                    TextEventLabel.source == row.source,
                    TextEventLabel.event_id == row.event_id,
                )
            )
            if _needs_work(view, label):
                found.append(view)
                seen.add(key)
                if len(found) >= limit:
                    return

    # 每类先取一部分，保证分布
    per = max(5, limit // max(1, len(prefer)))
    for source in prefer:
        if len(found) >= limit:
            break
        rows = db.scalars(
            select(TextEvent).where(TextEvent.source == source)
            .order_by(TextEvent.publish_date.desc(), TextEvent.id.desc())
            .limit(per)
        ).all()
        _take(list(rows))
    if len(found) < limit:
        rows = db.scalars(
            select(TextEvent).where(TextEvent.source.notin_(list(skip_sources)))
            .order_by(TextEvent.publish_date.desc(), TextEvent.id.desc())
            .limit(min(scan_cap, limit * 20))
        ).all()
        _take(list(rows))
    return found[:limit]



def _needs_work(event: EventView, label: TextEventLabel | None) -> bool:
    digest = input_hash(
        source=event.source, event_id=event.event_id, code=event.code,
        publish_date=event.publish_date, title=event.title, body=event.body,
    )
    rule = procedural_rule(event.title)
    if label is None or label.status != "ok" or label.input_hash != digest:
        return True
    if rule is not None and label.label_origin != "rule":
        return True
    if rule is None and label.label_origin == "rule":
        return True
    return False


def _existing(db: Session, event: EventView) -> TextEventLabel | None:
    return db.scalar(
        select(TextEventLabel).where(
            TextEventLabel.source == event.source,
            TextEventLabel.event_id == event.event_id,
            TextEventLabel.prompt_version == PROMPT_VERSION,
        )
    )


def _cache_hit(existing: TextEventLabel | None, digest: str, *, origin: str) -> bool:
    return bool(
        existing is not None
        and existing.status == "ok"
        and existing.input_hash == digest
        and existing.label_origin == origin
    )


def _write(db: Session, existing: TextEventLabel | None, event: EventView, digest: str,
           keywords: dict[str, str], fields: dict[str, Any]) -> TextEventLabel:
    payload = {
        "source": event.source,
        "event_id": event.event_id,
        "code": event.code,
        "publish_date": event.publish_date,
        "available_at": event.available_at,
        "prompt_version": PROMPT_VERSION,
        "input_hash": digest,
        "labeled_at": utcnow_naive(),
        **keywords,
        **fields,
    }
    if existing is None:
        row = TextEventLabel(**payload)
        db.add(row)
    else:
        for key, value in payload.items():
            setattr(existing, key, value)
        row = existing
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raced = _existing(db, event)
        if raced is None:
            raise
        return raced
    db.refresh(row)
    return row


def _from_row(row: TextEventLabel, event: EventView, *, cached: bool) -> LabelResult:
    return LabelResult(
        source=row.source,
        event_id=row.event_id,
        status="cached" if cached else row.status,
        label=row.label,
        category=row.category,
        score=row.score,
        confidence=row.confidence,
        rationale=row.rationale,
        keyword_label=row.keyword_label,
        label_origin=row.label_origin,
        latency_ms=row.latency_ms,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        model=row.model,
        cached=cached,
        title=event.title,
    )


def run_label_batch(
    db: Session,
    *,
    limit: int,
    settings: Any,
    completer: Completer | None = None,
    session_factory: Callable[[], Session] | None = None,
    workers: int | None = None,
) -> list[LabelResult]:
    """串行或有限并发。并发时每个事件单独开 session，重复插入按唯一键变成缓存命中。"""
    pending = pending_events(db, limit)
    if completer is None and bool(getattr(settings, "text_event_llm_enabled", False)):
        from app.services.text_events.llm_client import GatewayCompleter

        completer = GatewayCompleter(settings)
    worker_count = max(1, int(workers if workers is not None else getattr(settings, "text_event_llm_concurrency", 1) or 1))
    if worker_count == 1 or session_factory is None:
        return label_events(db, pending, settings=settings, completer=completer)
    from concurrent.futures import ThreadPoolExecutor

    def _one(event: EventView) -> LabelResult:
        session = session_factory()
        try:
            return label_event(session, event, settings=settings, completer=completer)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        return list(pool.map(_one, pending))
