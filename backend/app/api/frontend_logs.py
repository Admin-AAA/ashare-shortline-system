import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/frontend-logs", tags=["frontend-logs"])


class LogEntry(BaseModel):
    level: str = "info"
    category: str = "unknown"
    message: str = ""
    extra: dict[str, Any] | None = None


@router.post("")
async def ingest_frontend_logs(payload: list[LogEntry] | None = None):
    """接收前端关键日志（性能异常、错误）并写入服务端日志。支持单条或批量。"""
    if payload is None:
        return {"ok": True, "count": 0}

    entries = payload if isinstance(payload, list) else [payload]
    for entry in entries:
        level = entry.level
        category = entry.category
        message = entry.message
        extra = entry.extra or {}

        msg = f"[FRONTEND] [{category}] {message}"
        if extra:
            msg += f" | extra={extra}"

        if level == "error":
            logger.error(msg)
        elif level in ("warn", "perf"):
            logger.warning(msg)
        else:
            logger.info(msg)

    return {"ok": True, "count": len(entries)}
