"""沪深300基准曲线。读本地 index_daily，不在请求路径上打外部行情。"""

from __future__ import annotations

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.contracts import ContractRouter as APIRouter
from app.db.session import get_db
from app.services.benchmark_service import (
    MAX_ALIGNED_DATES,
    benchmark_curve_for_dates,
    benchmark_daily_curve,
)

router = APIRouter(prefix="/api/benchmarks", tags=["benchmarks"])


class Hs300AlignedRequest(BaseModel):
    dates: list[str] = Field(default_factory=list, max_length=MAX_ALIGNED_DATES)


@router.get("/hs300")
def hs300_daily_curve(
    start: str,
    end: str,
    db: Session = Depends(get_db),
):
    """区间内沪深300交易日累计收益，首个交易日归 0% / 净值 1。"""
    try:
        return benchmark_daily_curve(db, start, end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/hs300/aligned")
def hs300_aligned_curve(
    body: Hs300AlignedRequest,
    db: Session = Depends(get_db),
):
    """按收益曲线的日期对齐沪深300累计收益。点的顺序与请求 dates 一致。"""
    try:
        return benchmark_curve_for_dates(db, body.dates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
