import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.workbench_service import (
    backtest_skill as backtest_skill_service,
    get_skill_detail as get_skill_detail_service,
    install_skill as install_skill_service,
    list_skills as list_skills_service,
    set_skill_mode as set_skill_mode_service,
    skill_performance_summary as skill_performance_summary_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.get("")
def list_skills(db: Session = Depends(get_db)):
    return list_skills_service(db)


@router.post("/install")
def install(payload: dict, db: Session = Depends(get_db)):
    try:
        return install_skill_service(db, payload)
    except ValueError as exc:
        logger.exception("install skill failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{skill_id}")
def skill_detail(skill_id: str, db: Session = Depends(get_db)):
    detail = get_skill_detail_service(db, skill_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="skill not found")
    return detail


@router.post("/{skill_id}/mode")
def set_mode(skill_id: str, payload: dict, db: Session = Depends(get_db)):
    mode = str(payload.get("mode") or "")
    if mode not in {"formal", "experiment", "disabled"}:
        raise HTTPException(status_code=400, detail="mode must be formal, experiment, or disabled")
    result = set_skill_mode_service(db, skill_id, mode)
    if result is None:
        raise HTTPException(status_code=404, detail="skill not found")
    return result


@router.post("/{skill_id}/backtest")
def backtest(skill_id: str, db: Session = Depends(get_db)):
    """兼容旧入口：只返回已有推荐表现摘要，不执行真实回测。"""
    result = backtest_skill_service(db, skill_id)
    if result is None:
        raise HTTPException(status_code=404, detail="skill not found")
    return result


@router.get("/{skill_id}/performance-summary")
def performance_summary(skill_id: str, db: Session = Depends(get_db)):
    result = skill_performance_summary_service(db, skill_id)
    if result is None:
        raise HTTPException(status_code=404, detail="skill not found")
    return result
