"""[中长线] 中线 / 长线策略 API。"""
from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.api.contracts import ContractRouter as APIRouter
from app.db.session import get_db
from app.services import midterm_service as svc

router = APIRouter(prefix="/api/horizons", tags=["horizons"])

LONG_REJECTED = [
    {"name": "低波动+低估值月度轮动", "reason": "2022–2024 为正，但最近12个月每笔 −0.29%，PF 0.83"},
    {"name": "低波动高股息月度轮动（dv_ttm≥3%、低波动前50%）", "reason": "真实成交回测最近12个月每笔 −0.02%、PF 0.99；2020/2022 每笔约 −1%；加年线/大盘MA60过滤后最近12个月仍为负（−0.7%~−1.4%）"},
    {"name": "价值（EP+BP）月度轮动（含60日动量/大盘过滤版）", "reason": "最近12个月每笔 −0.25%~−0.76%，2020 −2.5%、2022–23 −1.5%；仅 2024 好。ROE质量因子待财务数据补齐后复测"},
    {"name": "业绩预告漂移（长持40日）", "reason": "最近12个月每笔 −1.9%~+0.5%，2023 年 4–12 月每笔 −4.9%~−8.4%"},
    {"name": "ROE质量+估值月度轮动（年化ROE≥15%、负债率<60%、经营现金流>0、PE≤25；EP+ROE / ROE÷PB / 加年线 / 加净利增长 4个预设版本）", "reason": "按公告日时点财务数据、真实成交回测：最近12个月每笔 −0.36%~−1.99%（PF 0.59~0.91）；2020 每笔 −2.5%~−3.3%、2022-04~2023-03 每笔 −2.8%~−4.3%（回撤 −28%~−41%）；仅 2024、2025年1–9月为正"},
    {"name": "12-1 月动量", "reason": "最近12个月持有20天 PF 1.00，2022–2023 大幅为负"},
]


MID_REJECTED = [
    {"name": "底部缩量潜伏 + 60日线生命线", "reason": "多种量化口径（底部买/放量突破买、加MACD金叉过滤、MA60/MA20止损）最近12个月均为负或持平，2023 年大幅亏损"},
    {"name": "小市值月度轮动（20只）", "reason": "与周度版高度重叠；2024 年每笔 −1.03%，最近12个月 +0.95% 不优于周度版"},
    {"name": "均线多头+250日新高突破", "reason": "2023/2024 每笔 −5.8%/−6.3%，A股中线动量追高失效"},
    {"name": "小市值+20日动量（含小盘趋势/崩盘过滤各版本）", "reason": "无过滤最近12个月月度版每笔 +6.6%，但 2024 年 −2.3%~−3.7%；加 MA20/MA60/崩盘退出后 2023 或 2024 仍为负"},
    {"name": "小市值周度分批（无过滤旧版）", "reason": "按真实成交规则重测：2024 年每笔 +0.08%、逐日回撤 −18.7%，被加小盘趋势过滤的新版取代"},
    {"name": "业绩预告漂移（预增≥50%、近20日涨幅<15%、大盘等权指数>MA20，持有20日）", "reason": "最近12个月78笔 胜率52.6% 每笔+3.42% PF1.99 总+13.3% 回撤−6.5%，但 2023-04~12 每笔 −3.11%（PF0.40，−9.6%），2020/2021 也为负，8个阶段仅5个为正，且是24组参数里挑出的最好一组，判定不稳健"},
    {"name": "20日超跌反转", "reason": "最近12个月每笔 −2.6%（10日持有）"},
    {"name": "小市值周度：最小30只里选低换手/低波动/20日跌幅最大的10只", "reason": "2019–2026 八阶段预设3个版本（未调参）：低换手最近12个月每笔 −0.67%、2020 −0.46%；低波动最近12个月 −0.89%、2023-04~12 与 2024 为负；反转版 2020/2021 为负、2024 仅 +0.47%。都不如现行最小10只"},
    {"name": "北向资金持股变化", "reason": "港交所 2024-08 起停止披露每日/每周北向持股，只剩季度数据，策略无法实盘运行，不做"},
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
