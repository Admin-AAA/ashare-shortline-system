"""单股票矩阵分析 + 回测 API。"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db

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
