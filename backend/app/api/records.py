"""记录中心 API —— 运行推荐记录 + 矩阵运行记录，含 1/3/5 日 + 至今收益率。

用于"记录中心/复盘"页面：查看历史 run 与矩阵分析，点进去看推荐的股票及收益表现。
"""

import logging

from fastapi import Depends, HTTPException, Query
from app.api.contracts import ContractRouter as APIRouter
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
    limit: int = Query(default=100, ge=1, le=500),
    page: int | None = Query(default=None, ge=1, description="给出 page/page_size 时返回 {items,total,page,page_size}"),
    page_size: int | None = Query(default=None, ge=1, le=200),
    run_mode: str | None = Query(default=None),
    status: str | None = Query(default=None),
    trade_date_from: str | None = Query(default=None),
    trade_date_to: str | None = Query(default=None),
    preset_id: str | None = Query(default=None),
    fields: str | None = Query(default=None, pattern="^(full|slim)$"),
    db: Session = Depends(get_db),
):
    """推荐运行记录列表（轻量摘要，不含推荐股票明细）。

    兼容：不带 page/page_size 时与旧版完全一致（返回数组，前 limit 条）。
    带 page 或 page_size 时在最近 500 条内按筛选条件服务端分页，按创建时间倒序，
    返回 {items, total, page, page_size}；total 为筛选后总数。
    """
    from app.services.records_service import slim_run

    if page is None and page_size is None:
        rows = list_run_records(db, limit=limit)
        return [slim_run(r) for r in rows] if fields == "slim" else rows
    rows = list_run_records(db, limit=500)
    def keep(r: dict) -> bool:
        td = str(r.get("trade_date") or "")
        return (
            (run_mode is None or r.get("run_mode") == run_mode)
            and (status is None or r.get("status") == status)
            and (preset_id is None or r.get("preset_id") == preset_id)
            and (trade_date_from is None or td >= trade_date_from)
            and (trade_date_to is None or td <= trade_date_to)
        )
    filtered = [r for r in rows if keep(r)]
    page = page or 1
    page_size = page_size or 50
    items = filtered[(page - 1) * page_size: page * page_size]
    if fields == "slim":
        items = [slim_run(r) for r in items]
    return {"items": items, "total": len(filtered), "page": page, "page_size": page_size}


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
    analysis_type: str | None = Query(default=None, description="按分析类型筛选：analysis | backtest | single_stock"),
    exclude_analysis_type: str | None = Query(default=None, description="排除某分析类型（矩阵 tab 排除 single_stock）"),
    db: Session = Depends(get_db),
):
    """矩阵分析历史记录列表（分页，按 created_at 倒序）。"""
    return list_matrix_records(
        db, page=page, page_size=page_size, code=code,
        analysis_type=analysis_type, exclude_analysis_type=exclude_analysis_type,
    )


@router.get("/matrix/{record_id}")
def matrix_record_detail(record_id: int, db: Session = Depends(get_db)):
    """单条矩阵分析记录详情（含分析日到今天收益率）。"""
    detail = get_matrix_record_detail(db, record_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="matrix record not found")
    return detail
