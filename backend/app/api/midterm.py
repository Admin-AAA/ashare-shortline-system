"""[中长线] 中线 / 长线策略 API。"""
from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.api.contracts import ContractRouter as APIRouter
from app.db.session import get_db
from app.services import midterm_service as svc

router = APIRouter(prefix="/api/horizons", tags=["horizons"])

LONG_REJECTED = [
    {"name": "低波动+低估值月度轮动", "reason": "2022–2024 为正，但最近12个月每笔 −0.29%，PF 0.83"},
    {"name": "12-1 月动量", "reason": "最近12个月持有20天 PF 1.00，2022–2023 大幅为负"},
]


MID_REJECTED = [
    {"name": "底部缩量潜伏 + 60日线生命线", "reason": "多种量化口径（底部买/放量突破买、加MACD金叉过滤、MA60/MA20止损）最近12个月均为负或持平，2023 年大幅亏损"},
    {"name": "小市值月度轮动（20只）", "reason": "与周度版高度重叠；2024 年每笔 −1.03%，最近12个月 +0.95% 不优于周度版"},
    {"name": "均线多头+250日新高突破", "reason": "2023/2024 每笔 −5.8%/−6.3%，A股中线动量追高失效"},
    {"name": "小市值+20日动量", "reason": "最近12个月每笔 +6.6% 很高，但 2024 年每笔 −2.9%，稳健性不过关，继续观察"},
    {"name": "20日超跌反转", "reason": "最近12个月每笔 −2.6%（10日持有）"},
]


@router.get("/mid/strategies")
def mid_strategies():
    return {"horizon": "mid", "strategies": svc.strategies_overview(), "rejected": MID_REJECTED}


@router.get("/mid/picks")
def mid_picks(strategy_id: str | None = Query(None), limit: int = Query(30, ge=1, le=200), db: Session = Depends(get_db)):
    return {"batches": svc.list_picks(db, strategy_id, limit)}


@router.get("/long/strategies")
def long_strategies():
    return {"horizon": "long", "strategies": [], "rejected": LONG_REJECTED}
