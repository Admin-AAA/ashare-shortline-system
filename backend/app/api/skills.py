import logging

from fastapi import Depends, HTTPException
from app.api.contracts import ContractRouter as APIRouter
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.workbench_service import (
    backtest_skill as backtest_skill_service,
    batch_skill_performance_summaries as batch_skill_performance_summaries_service,
    get_skill_detail as get_skill_detail_service,
    install_skill as install_skill_service,
    list_skills as list_skills_service,
    set_skill_enabled as set_skill_enabled_service,
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


@router.get("/performance-summaries")
def batch_performance_summaries(db: Session = Depends(get_db)):
    """批量获取所有 skill 的 performance summary。"""
    return batch_skill_performance_summaries_service(db)


@router.get("/{skill_id}")
def skill_detail(skill_id: str, db: Session = Depends(get_db)):
    detail = get_skill_detail_service(db, skill_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="skill not found")
    return detail


@router.post("/{skill_id}/enabled")
def set_enabled(skill_id: str, payload: dict, db: Session = Depends(get_db)):
    enabled = bool(payload.get("enabled"))
    result = set_skill_enabled_service(db, skill_id, enabled)
    if result is None:
        raise HTTPException(status_code=404, detail="skill not found")
    return result


@router.post("/{skill_id}/mode")
def set_mode_deprecated(skill_id: str, payload: dict, db: Session = Depends(get_db)):
    """已废弃：请使用 POST /api/skills/{id}/enabled。"""
    enabled = str(payload.get("mode") or "") != "disabled"
    result = set_skill_enabled_service(db, skill_id, enabled)
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
