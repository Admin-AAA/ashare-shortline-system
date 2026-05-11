from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.outcome_service import metrics_by_strategy, overview_metrics

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    return overview_metrics(db)


@router.get("/by-strategy")
def by_strategy(db: Session = Depends(get_db)):
    return metrics_by_strategy(db)


@router.get("/by-setup")
def by_setup():
    return []


@router.get("/precision-at-k")
def precision_at_k():
    return [{"k": 3, "precision": 0}, {"k": 5, "precision": 0}, {"k": 10, "precision": 0}]

