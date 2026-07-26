from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.outcome_service import audit_trading_cost_boundary_labels, recompute_stale_outcomes
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
    db: Session = Depends(get_db),
):
    return label_pending_outcomes(db, horizon_days=horizon_days)


@router.post("/recompute")
def recompute(
    horizon_days: int = Query(default=5, ge=1, le=20),
    limit: int | None = Query(default=None, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    """重算已标注 outcome（simulation 逻辑变更后的数据一致性维护）。

    用于 _resolve_exit / _simulate_outcome 逻辑变更后让历史 outcome 反映新逻辑。
    """
    return recompute_stale_outcomes(db, horizon_days=horizon_days, limit=limit)


@router.post("/audit-trading-cost-policy")
def audit_trading_cost_policy(db: Session = Depends(get_db)):
    return audit_trading_cost_boundary_labels(db)
