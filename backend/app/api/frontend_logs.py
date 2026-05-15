import logging
from typing import Any

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/frontend-logs", tags=["frontend-logs"])


@router.post("")
async def ingest_frontend_logs(request: Request):
    """接收前端关键日志（性能异常、错误）并写入服务端日志。支持单条或批量。"""
    body = await request.json()
    entries: list[dict[str, Any]] = body if isinstance(body, list) else [body]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        level = entry.get("level", "info")
        category = entry.get("category", "unknown")
        message = entry.get("message", "")
        extra = entry.get("extra", {})

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
