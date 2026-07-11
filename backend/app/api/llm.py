"""LLM 审查层相关接口。

当前唯一端点：GET /api/llm/review-health —— 返回 LLM 审查服务新鲜度，
把「AI 审查层是否仍在真实工作」变成可观测指标（北极星 B 项可见性缺口修复）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.llm_review_cache_service import llm_review_health

router = APIRouter(prefix="/api/llm", tags=["llm"])


@router.get("/review-health")
def get_llm_review_health(db: Session = Depends(get_db)) -> dict:
    """LLM 审查服务健康度（新鲜度代理）。

    返回：
      - last_review_at：最近一次成功 LLM 审查时间（ISO）
      - recent_count_24h：近 24h 成功审查次数
      - total_entries：缓存总条数
      - model：最近一次审查使用的模型
      - status：ok / stale / never（见 llm_review_cache_service._llm_health_status）
      - checked_at：本端点查询时间

    用途：模型 key 过期 / provider 故障时，审查层静默停摆，推荐会缺 LLM delta
    而用户无感知。此端点让该状态在驾驶舱可见。
    """
    return llm_review_health(db)
