from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.outcome_service import (
    filter_effectiveness,
    metrics_by_score_line,
    metrics_by_setup,
    metrics_by_strategy,
    overview_metrics,
    precision_at_k,
)

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


def _checked_mode(mode: str) -> str:
    if mode not in {"formal", "experiment"}:
        raise HTTPException(status_code=400, detail="mode must be formal or experiment")
    return mode


@router.get("/overview")
def overview(mode: str = Query(default="formal"), db: Session = Depends(get_db)):
    return overview_metrics(db, _checked_mode(mode))


@router.get("/by-strategy")
def by_strategy(mode: str = Query(default="formal"), db: Session = Depends(get_db)):
    return metrics_by_strategy(db, _checked_mode(mode))


@router.get("/by-setup")
def by_setup(mode: str = Query(default="formal"), db: Session = Depends(get_db)):
    return metrics_by_setup(db, _checked_mode(mode))


@router.get("/precision-at-k")
def metric_precision_at_k(mode: str = Query(default="formal"), db: Session = Depends(get_db)):
    return precision_at_k(db, _checked_mode(mode))


@router.get("/by-score-line")
def by_score_line(mode: str = Query(default="formal"), db: Session = Depends(get_db)):
    return metrics_by_score_line(db, _checked_mode(mode))


@router.get("/filter-effectiveness")
def metric_filter_effectiveness(mode: str = Query(default="formal"), db: Session = Depends(get_db)):
    return filter_effectiveness(db, _checked_mode(mode))
