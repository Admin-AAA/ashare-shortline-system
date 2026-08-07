from typing import Literal

from fastapi import Depends, Query
from app.api.contracts import ContractRouter as APIRouter
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.official_sample_scope import LIVE_FORWARD_SCOPE
from app.services.outcome_service import alpha_decay_detection
from app.services.stats_service import get_overview_stats, get_preset_stats, get_skill_stats

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    return get_overview_stats(db)


@router.get("/by-skill")
def by_skill(db: Session = Depends(get_db)):
    return get_skill_stats(db)


@router.get("/by-preset")
def by_preset(db: Session = Depends(get_db)):
    return get_preset_stats(db)


@router.get("/alpha-decay")
def alpha_decay(
    db: Session = Depends(get_db),
    window_days: int | None = Query(
        default=None,
        ge=1,
        le=365,
        description="滚动窗口天数；设置后自动截到已结算截止日",
    ),
    start_date: str | None = Query(
        default=None,
        description="显式起始日期 YYYY-MM-DD（window_days 优先）",
    ),
    end_date: str | None = Query(
        default=None,
        description="显式截止日期 YYYY-MM-DD（window_days 优先）",
    ),
    sample_scope: Literal["live_forward", "historical_replay"] = Query(
        default=LIVE_FORWARD_SCOPE,
        description="样本口径：LIVE 前瞻或显式历史重建研究证据",
    ),
):
    """Alpha 衰减检测：对比近期 vs 早期各因子的 point-biserial 相关系数。"""
    return alpha_decay_detection(
        db,
        sample_scope=sample_scope,
        window_days=window_days,
        start_date=start_date,
        end_date=end_date,
    )
