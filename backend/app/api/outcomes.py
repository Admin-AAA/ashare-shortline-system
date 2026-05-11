from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.workbench_service import label_outcome_manually, label_pending_outcomes

router = APIRouter(prefix="/api/outcomes", tags=["outcomes"])


@router.post("/{recommendation_id}/label")
def label_one(recommendation_id: str, payload: dict, db: Session = Depends(get_db)):
    outcome = label_outcome_manually(db, recommendation_id, payload)
    if outcome is None:
        raise HTTPException(status_code=404, detail="recommendation not found")
    return outcome


@router.post("/label")
def label_many(
    horizon_days: int = Query(default=5, ge=1, le=20),
    run_mode: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    if run_mode is not None and run_mode not in {"formal", "experiment"}:
        raise HTTPException(status_code=400, detail="run_mode must be formal or experiment")
    return label_pending_outcomes(db, horizon_days=horizon_days, run_mode=run_mode)
