from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
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
def alpha_decay(db: Session = Depends(get_db)):
    """Alpha 衰减检测：对比近期 vs 早期各因子的 point-biserial 相关系数。"""
    return alpha_decay_detection(db)
