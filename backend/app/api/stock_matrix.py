"""单股票矩阵分析 + 回测 API。"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, desc
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.tables import StockMatrixHistory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stock-matrix", tags=["stock-matrix"])


@router.post("/analysis")
def stock_matrix_analysis(payload: dict, db: Session = Depends(get_db)):
    """单股票全 Skill 矩阵分析。

    请求体:
        code: str           # 股票代码（必填）
        trade_date: str     # 分析日期（默认最近交易日）
        include_llm: bool   # 是否触发 LLM agent（默认 False）
        skill_ids: list     # 限定 skill 列表（默认全部）
    """
    from app.services.stock_matrix_service import analyze_stock_matrix

    code = str(payload.get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="code is required")

    trade_date = str(payload.get("trade_date") or "").strip() or None
    include_llm = bool(payload.get("include_llm", False))
    skill_ids = payload.get("skill_ids")

    try:
        return analyze_stock_matrix(
            db, code,
            trade_date=trade_date,
            include_llm=include_llm,
            skill_ids=skill_ids,
        )
    except Exception as exc:
        logger.exception("stock matrix analysis failed for %s", code)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/backtest")
def stock_matrix_backtest(payload: dict, db: Session = Depends(get_db)):
    """单股票全 Skill 历史回测。

    请求体:
        code: str           # 股票代码（必填）
        start_date: str     # 起始日期（必填）
        end_date: str       # 结束日期（必填）
        horizon_days: int   # 持仓天数（默认 5）
        skill_ids: list     # 限定 skill 列表（默认全部）
    """
    from app.services.stock_backtest_service import run_stock_backtest

    code = str(payload.get("code") or "").strip()
    start_date = str(payload.get("start_date") or "").strip()
    end_date = str(payload.get("end_date") or "").strip()

    if not code:
        raise HTTPException(status_code=400, detail="code is required")
    if not start_date or not end_date:
        raise HTTPException(status_code=400, detail="start_date and end_date are required")
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must be <= end_date")

    horizon_days = int(payload.get("horizon_days", 5))
    skill_ids = payload.get("skill_ids")

    try:
        return run_stock_backtest(
            db, code, start_date, end_date,
            horizon_days=horizon_days,
            skill_ids=skill_ids,
        )
    except Exception as exc:
        logger.exception("stock matrix backtest failed for %s", code)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/history")
def stock_matrix_history(
    code: str = Query(..., description="股票代码"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(10, ge=1, le=50, description="每页条数"),
    db: Session = Depends(get_db),
):
    """查询单股票矩阵分析历史记录（分页）。"""
    code = code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="code is required")

    total = db.scalar(
        select(func.count()).select_from(StockMatrixHistory)
        .where(StockMatrixHistory.code == code)
    ) or 0

    offset = (page - 1) * page_size
    rows = db.scalars(
        select(StockMatrixHistory)
        .where(StockMatrixHistory.code == code)
        .order_by(desc(StockMatrixHistory.created_at))
        .offset(offset)
        .limit(page_size)
    ).all()

    items = [
        {
            "id": row.id,
            "code": row.code,
            "trade_date": row.trade_date,
            "analysis_type": row.analysis_type,
            "final_score": row.final_score,
            "win_rate_est": row.win_rate_est,
            "avg_return_est": row.avg_return_est,
            "skill_count": row.skill_count,
            "elapsed_ms": row.elapsed_ms,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]

    return {
        "code": code,
        "page": page,
        "page_size": page_size,
        "total": int(total),
        "items": items,
    }
