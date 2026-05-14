from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.workbench_service import delete_recommendation, get_recommendation_detail, save_review_note, set_watch_status

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


@router.get("/{recommendation_id}")
def detail(recommendation_id: str, db: Session = Depends(get_db)):
    rec = get_recommendation_detail(db, recommendation_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="recommendation not found")
    return rec


@router.post("/{recommendation_id}/watch")
def watch(recommendation_id: str, payload: dict, db: Session = Depends(get_db)):
    result = set_watch_status(
        db,
        recommendation_id=recommendation_id,
        watch_status=str(payload.get("watch_status") or "watching"),
        note=payload.get("note"),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="recommendation not found")
    return result


@router.post("/{recommendation_id}/ignore")
def ignore(recommendation_id: str, payload: dict | None = None, db: Session = Depends(get_db)):
    result = set_watch_status(
        db,
        recommendation_id=recommendation_id,
        watch_status="ignored",
        note=(payload or {}).get("note"),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="recommendation not found")
    return result


@router.delete("/{recommendation_id}")
def delete(recommendation_id: str, db: Session = Depends(get_db)):
    ok = delete_recommendation(db, recommendation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="recommendation not found")
    return {"deleted": recommendation_id}


@router.post("/{recommendation_id}/review")
def review(recommendation_id: str, payload: dict, db: Session = Depends(get_db)):
    note = save_review_note(db, recommendation_id, payload)
    if note is None:
        raise HTTPException(status_code=404, detail="recommendation not found")
    return note
