"""记录中心 API —— 运行推荐记录 + 矩阵运行记录，含 1/3/5 日 + 至今收益率。

用于"记录中心/复盘"页面：查看历史 run 与矩阵分析，点进去看推荐的股票及收益表现。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.records_service import (
    get_matrix_record_detail,
    get_run_record_detail,
    list_matrix_records,
    list_run_records,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/records", tags=["records"])


@router.get("/runs")
def run_records(
    run_mode: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """推荐运行记录列表（轻量摘要，不含推荐股票明细）。"""
    if run_mode is not None and run_mode not in {"formal", "experiment"}:
        raise HTTPException(status_code=400, detail="run_mode must be formal or experiment")
    return list_run_records(db, run_mode=run_mode, limit=limit)


@router.get("/runs/{run_id}")
def run_record_detail(run_id: str, db: Session = Depends(get_db)):
    """单次推荐运行详情 + 当次推荐股票（含 1/3/5日 + 至今收益率）。"""
    detail = get_run_record_detail(db, run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="run not found")
    return detail


@router.get("/matrix")
def matrix_records(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    code: str | None = Query(default=None, description="按股票代码筛选"),
    db: Session = Depends(get_db),
):
    """矩阵分析历史记录列表（分页，按 created_at 倒序）。"""
    return list_matrix_records(db, page=page, page_size=page_size, code=code)


@router.get("/matrix/{record_id}")
def matrix_record_detail(record_id: int, db: Session = Depends(get_db)):
    """单条矩阵分析记录详情（含分析日到今天收益率）。"""
    detail = get_matrix_record_detail(db, record_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="matrix record not found")
    return detail
