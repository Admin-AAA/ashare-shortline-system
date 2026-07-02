from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.outcome_service import alpha_decay_detection
from app.services.stats_service import get_overview_stats, get_preset_stats, get_skill_stats

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _checked_mode(mode: str) -> str:
    if mode not in {"formal", "experiment"}:
        raise HTTPException(status_code=400, detail="mode must be formal or experiment")
    return mode


@router.get("/overview")
def overview(mode: str = Query(default="formal"), db: Session = Depends(get_db)):
    return get_overview_stats(db, _checked_mode(mode))


@router.get("/by-skill")
def by_skill(mode: str = Query(default="formal"), db: Session = Depends(get_db)):
    return get_skill_stats(db, _checked_mode(mode))


@router.get("/by-preset")
def by_preset(mode: str = Query(default="formal"), db: Session = Depends(get_db)):
    return get_preset_stats(db, _checked_mode(mode))


@router.get("/alpha-decay")
def alpha_decay(mode: str = Query(default="formal"), db: Session = Depends(get_db)):
    """Alpha 衰减检测：对比近期 vs 早期各因子的 point-biserial 相关系数。"""
    return alpha_decay_detection(db, run_mode=_checked_mode(mode))
