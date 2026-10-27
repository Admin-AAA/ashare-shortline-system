"""文本事件标签只读接口。不写库，不改变推荐。"""
from __future__ import annotations

from fastapi import Depends, HTTPException
from app.api.contracts import ContractRouter as APIRouter
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.text_events.query import list_labels_for_code

router = APIRouter(prefix="/api/text-events", tags=["text-events"])


@router.get("/labels")
def get_text_event_labels(code: str, limit: int = 20, db: Session = Depends(get_db)) -> dict:
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(status_code=400, detail="code 必须是 6 位数字")
    return list_labels_for_code(db, code, limit=limit)
