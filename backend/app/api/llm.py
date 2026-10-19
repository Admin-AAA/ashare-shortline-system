"""LLM 审查层相关接口。

当前唯一端点：GET /api/llm/review-health —— 返回 LLM 审查服务新鲜度，
把「AI 审查层是否仍在真实工作」变成可观测指标（北极星 B 项可见性缺口修复）。
"""

from __future__ import annotations

from fastapi import Depends
from app.api.contracts import ContractRouter as APIRouter
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.llm_review_cache_service import llm_review_health, recent_llm_call_health

router = APIRouter(prefix="/api/llm", tags=["llm"])


@router.get("/review-health")
def get_llm_review_health(db: Session = Depends(get_db)) -> dict:
    """LLM 审查服务健康度（新鲜度代理 + 调用成功率）。

    返回：
      - last_review_at：最近一次成功 LLM 审查时间（ISO）
      - recent_count_24h：近 24h 成功审查次数
      - total_entries：缓存总条数
      - model：最近一次审查使用的模型
      - status：ok / stale / never / disabled（停用时固定 disabled，见 _llm_health_status）
      - enabled：是否启用 AI 风险审查（与 async_llm_risk_notes.LLM_RISK_NOTES_ENABLED 同源）
      - checked_at：本端点查询时间
      - call_health：最近5个全市场 formal run 的 skill_review 调用成功率 + 失败原因
        （run 级过程健康，与缓存新鲜度正交：缓存命中旧 cache 时新鲜度 ok 但新 run
        可能全失败）

    用途：模型 key 过期 / provider 故障 / 熔断 / 余额不足时，审查层静默停摆，推荐缺
    LLM delta 而用户无感知。此端点让该状态在驾驶舱可见。
    """
    health = llm_review_health(db)
    health["call_health"] = recent_llm_call_health(db)
    return health


@router.get("/route-health")
async def get_llm_route_health() -> list[dict]:
    """模型路由可用性检查：对每个 enabled 路由发 GET /models（轻量，不消耗 chat 额度）。

    返回每路由的 reachable / status_code / model_count / error。让用户看到 base_url
    是否可达、key 是否有效——07-22 LLM 65% 失败时定位是 grok-4.5 还是 kimi-k3 的问题，
    以及 kimi-k3 base_url=api.kimi.com/coding/v1 是否正确（标准 api.moonshot.cn/v1）。

    异步端点 + 5s 超时 + 并发检查，不阻塞主线程。
    """
    from app.services.model_router_service import check_route_availability
    return await check_route_availability()
