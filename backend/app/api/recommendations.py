from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.workbench_service import (
    delete_recommendation,
    get_recommendation_detail,
    get_run_replay_analysis,
    list_recommendation_history,
    save_review_note,
    set_watch_status,
)

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


@router.get("/history")
def history(
    run_mode: str = Query(default="formal"),
    code: str | None = Query(default=None),
    label: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """跨 run 推荐历史（带 outcome），支持分页 + 筛选。

    填补推荐历史记录页缺口：用户查看所有推荐过的股票及其实际表现。
    """
    if run_mode not in {"formal", "experiment"}:
        raise HTTPException(status_code=400, detail="run_mode must be formal or experiment")
    if label is not None and label not in {"success", "failed", "missed", "pending"}:
        raise HTTPException(status_code=400, detail="label must be success/failed/missed/pending")
    return list_recommendation_history(
        db, run_mode=run_mode, code=code, label=label, limit=limit, offset=offset
    )


@router.get("/runs/{run_id}/replay-analysis")
def replay_analysis(run_id: str, db: Session = Depends(get_db)):
    result = get_run_replay_analysis(db, run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="run not found")
    return result


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
