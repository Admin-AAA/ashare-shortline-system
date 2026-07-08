from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.outcome_service import (
    filter_effectiveness,
    metrics_by_score_line,
    metrics_by_setup,
    metrics_by_strategy,
    overview_metrics,
    precision_at_k,
)

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


# 时间窗口通用参数（每个端点必须用独立 Query() 实例，FastAPI 不允许复用同一对象）。
# - window_days: 滚动窗口天数（如 30/90）。设置后自动 start=today-N, end=today-SETTLE_LAG(20天)，
#   保证窗口内推荐均已走完 horizon 结算，避免 pending 污染样本。
# - start_date / end_date: 显式 ISO 日期（YYYY-MM-DD），优先级低于 window_days。
#   不传则统计全部历史累计。
def _window_days_q() -> "Query":
    return Query(default=None, ge=1, le=365, description="滚动窗口天数，如 30/90；设置后自动截到已结算截止日")


def _start_date_q() -> "Query":
    return Query(default=None, description="显式起始日期 YYYY-MM-DD（window_days 优先）")


def _end_date_q() -> "Query":
    return Query(default=None, description="显式截止日期 YYYY-MM-DD（window_days 优先）")


@router.get("/overview")
def overview(
    db: Session = Depends(get_db),
    window_days: int | None = _window_days_q(),
    start_date: str | None = _start_date_q(),
    end_date: str | None = _end_date_q(),
):
    return overview_metrics(db, window_days=window_days, start_date=start_date, end_date=end_date)


@router.get("/by-strategy")
def by_strategy(
    db: Session = Depends(get_db),
    window_days: int | None = _window_days_q(),
    start_date: str | None = _start_date_q(),
    end_date: str | None = _end_date_q(),
):
    return metrics_by_strategy(db, window_days=window_days, start_date=start_date, end_date=end_date)


@router.get("/by-setup")
def by_setup(
    db: Session = Depends(get_db),
    window_days: int | None = _window_days_q(),
    start_date: str | None = _start_date_q(),
    end_date: str | None = _end_date_q(),
):
    return metrics_by_setup(db, window_days=window_days, start_date=start_date, end_date=end_date)


@router.get("/precision-at-k")
def metric_precision_at_k(
    db: Session = Depends(get_db),
    window_days: int | None = _window_days_q(),
    start_date: str | None = _start_date_q(),
    end_date: str | None = _end_date_q(),
):
    return precision_at_k(db, window_days=window_days, start_date=start_date, end_date=end_date)


@router.get("/by-score-line")
def by_score_line(
    db: Session = Depends(get_db),
    window_days: int | None = _window_days_q(),
    start_date: str | None = _start_date_q(),
    end_date: str | None = _end_date_q(),
):
    return metrics_by_score_line(db, window_days=window_days, start_date=start_date, end_date=end_date)


@router.get("/filter-effectiveness")
def metric_filter_effectiveness(
    db: Session = Depends(get_db),
    window_days: int | None = _window_days_q(),
    start_date: str | None = _start_date_q(),
    end_date: str | None = _end_date_q(),
):
    return filter_effectiveness(db, window_days=window_days, start_date=start_date, end_date=end_date)
