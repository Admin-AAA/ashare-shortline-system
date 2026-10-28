"""只读查询。没有任何写入，也不读取选股结果。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market import TextEventLabel
from app.services.text_events.schema import PROMPT_VERSION

DISCLAIMER = "仅供参考，不参与选股"


def list_labels_for_code(db: Session, code: str, *, limit: int = 20) -> dict:
    rows = db.scalars(
        select(TextEventLabel)
        .where(TextEventLabel.code == code, TextEventLabel.status == "ok")
        .order_by(TextEventLabel.publish_date.desc(), TextEventLabel.id.desc())
        .limit(max(1, min(limit, 100)))
    ).all()
    return {
        "disclaimer": DISCLAIMER,
        "participates_in_selection": False,
        "prompt_version": PROMPT_VERSION,
        "code": code,
        "items": [
            {
                "source": row.source,
                "event_id": row.event_id,
                "publish_date": row.publish_date,
                "available_at": row.available_at,
                "label": row.label,
                "category": row.category,
                "score": row.score,
                "confidence": row.confidence,
                "rationale": row.rationale,
                "model": row.model,
                "prompt_version": row.prompt_version,
                "label_origin": row.label_origin,
                "keyword_label": row.keyword_label,
                "labeled_at": row.labeled_at.isoformat(sep=" ", timespec="seconds") if row.labeled_at else None,
            }
            for row in rows
        ],
    }
