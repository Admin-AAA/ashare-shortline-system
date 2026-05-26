"""
陈小群龙头战法 — 情绪周期检测 + 仓位管理 + 止损纪律。

依赖上游框架 https://github.com/sherjy/chen-xiaoqun-skill/blob/main/SKILL.md。
"""

from __future__ import annotations

import enum
import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import DailyKbar, LimitUpBoard

logger = logging.getLogger(__name__)

# ── 情绪周期阈值（可调） ──
# 基准：2026-01~2026-05 共 42 个有 limit_up_board 数据的交易日，涨停数 P90=101、P75=90，
# 跌停 P90=35，连板高度均值 5.1，晋级率均值 17%、P75=20%、最大值 32%。
# 具体依据见 scripts/diagnose_cxsq_cycle.py 诊断输出。

_MIN_LIMIT_DOWN_FOR_RETREAT = 50         # 跌停 >= 50 → 退潮（P90=35，50以上仅极端行情）
_MAX_HEIGHT_FOR_RETREAT = 2              # 连板高度 <= 2 → 退潮
_MIN_HEIGHT_FOR_STARTUP = 3              # 连板高度 >= 3 → 启动
_MIN_LIMIT_UP_FOR_FERMENT = 5            # 涨停 >= 5 → 发酵候选（连续达标）
_MIN_PROMOTION_RATE_FOR_FERMENT = 0.18   # 晋级率 > 18% → 发酵确认。
                                         #   原 40% 在实际数据中最大值仅 32%，从未触发。
                                         #   均值 17%、P50=16%、P75=20%，18% 为合理分界。
_MIN_LIMIT_UP_FOR_CLIMAX = 85            # 涨停 > 85 → 高潮候选。
                                         #   原 100 仅在 P90 以上~10% 交易日可达，降至 85
                                         #   可覆盖涨停数优势日（P75=90）同时配合高度>=5
                                         #   防止普涨误判。
_MIN_HEIGHT_FOR_CLIMAX = 5               # 连板高度 >= 5 → 高潮确认
_MIN_TIER_2_BOARD = 8                    # 2板最少家数（梯队完整性）
_MIN_TIER_3_BOARD = 3                    # 3板最少家数（梯队完整性）


class EmotionCycle(str, enum.Enum):
    """情绪周期四阶段，与 SKILL.md 映射一致。"""
    STARTUP = "启动"       # 冰点修复，连板高度回升
    FERMENT = "发酵"       # 梯队展开，题材被认可
    CLIMAX = "高潮"        # 一致看多，连板遍地开花
    RETREAT = "退潮"       # 龙头首阴被砸，亏钱效应扩散


def detect_emotion_cycle(snapshot_date: str, db: Session) -> EmotionCycle:
    """检测当日所属情绪周期阶段。

    判断依据（来自上游框架）：
      - 退潮：连板高度 <= 2 OR 跌停 > 50 只
      - 启动：连板高度从 <= 2 回升到 >= 3（与前一日对比，简化版只检查 >=3）
      - 发酵：涨停 >= 5 只 AND 连板晋级率 > 18%
      - 高潮：涨停 > 85 只 AND 连板高度 >= 5

    当多个阶段条件同时满足时，优先级：退潮 > 高潮 > 发酵 > 启动 > 默认发酵。

    Args:
        snapshot_date: 交易日（YYYY-MM-DD）
        db: 数据库会话

    Returns:
        当前情绪周期阶段
    """
    # ── 1. 获取当日涨停/跌停统计 ──
    limit_up_count = _count_limit_up(db, snapshot_date)       # 涨停家数
    limit_down_count = _count_limit_down(db, snapshot_date)   # 跌停家数
    max_consecutive = _max_limit_up_consecutive(db, snapshot_date)  # 当日最大连板高度

    # ── 1b. 数据完整性检查 ──
    # limit_up_board 数据非每日全量拉取，当库中无当日数据时涨停数和连板高度均为 0，
    # 会被退潮条件（高度 <= 2）误判为退潮。诊断显示 49/91 个交易日无数据被误标记。
    # 当涨停数和连板高度同时为 0 时，保守默认启动期，而非退潮。
    if limit_up_count == 0 and max_consecutive == 0:
        logger.debug("limit_up_count=0 & max_consecutive=0 on %s → 默认启动期", snapshot_date)
        return EmotionCycle.STARTUP

    # ── 2. 逐级判断 ──
    # 退潮（最高优先级）
    if max_consecutive <= _MAX_HEIGHT_FOR_RETREAT or limit_down_count > _MIN_LIMIT_DOWN_FOR_RETREAT:
        return EmotionCycle.RETREAT

    # 高潮
    if limit_up_count > _MIN_LIMIT_UP_FOR_CLIMAX and max_consecutive >= _MIN_HEIGHT_FOR_CLIMAX:
        return EmotionCycle.CLIMAX

    # 发酵
    if limit_up_count >= _MIN_LIMIT_UP_FOR_FERMENT:
        promotion_rate = _compute_promotion_rate(db, snapshot_date)
        if promotion_rate > _MIN_PROMOTION_RATE_FOR_FERMENT and _count_tier_completeness(db, snapshot_date):
            return EmotionCycle.FERMENT

    # 启动
    if max_consecutive >= _MIN_HEIGHT_FOR_STARTUP:
        return EmotionCycle.STARTUP

    # 默认：若数据量太少无法判断，认为是启动期试错
    return EmotionCycle.STARTUP


def get_position_pct(cycle: EmotionCycle) -> float:
    """根据情绪周期返回建议仓位比例（0~1）。

    Returns:
        STARTUP: 0.15, FERMENT: 0.40, CLIMAX: 0.60, RETREAT: 0.0
    """
    mapping = {
        EmotionCycle.STARTUP: 0.15,
        EmotionCycle.FERMENT: 0.40,
        EmotionCycle.CLIMAX: 0.60,
        EmotionCycle.RETREAT: 0.0,
    }
    return mapping.get(cycle, 0.0)


def apply_stop_loss(
    trade: dict[str, Any],
    current_price: float,
    entry_price: float,
    days_held: int,
) -> dict[str, Any]:
    """应用陈小群止损纪律。

    规则（按优先级）：
      1. 单日跌 > 5% → 强制止损（stop_loss）
      2. 持仓 3 天不涨 → 减半（half）
      3. 高位放量滞涨 → 出（exit）

    Args:
        trade: 交易记录 dict（须含 daily_volume 等字段用于判断放量滞涨）
        current_price: 当前价格
        entry_price: 买入价格
        days_held: 已持仓交易日数

    Returns:
        dict with keys:
          - action: "stop_loss" | "half" | "exit" | "hold"
          - reason: 触发原因
    """
    daily_return = (current_price - entry_price) / entry_price * 100.0

    # 规则 1：单日跌 > 5%
    # 比较 current_price 相对于前一日收盘的变化。若无法获取前一日收盘，
    # 使用 entry_price 近似判断最大回撤
    if daily_return < -5.0:
        return {"action": "stop_loss", "reason": f"单日跌幅 {daily_return:.1f}% > 5%，强制止损"}

    # 规则 2：持仓 3 天不涨 → 减半（每个持仓只减半一次）
    if days_held >= 3 and daily_return <= 0 and not trade.get("already_halved"):
        return {"action": "half", "reason": f"持仓 {days_held} 天涨幅 {daily_return:.1f}% <= 0，减半控险"}

    # 规则 3：高位放量滞涨
    # 条件：涨幅很小（< 2%）但成交量大（需通过 trade 参数传入）
    if abs(daily_return) < 2.0:
        signal_day_volume = trade.get("daily_volume", 0) or trade.get("signal_day_volume", 0)
        current_volume = trade.get("current_volume", 0)
        if signal_day_volume > 0 and current_volume > signal_day_volume * 1.5:
            return {
                "action": "exit",
                "reason": f"放量滞涨：涨幅 {daily_return:.1f}% 但量能达信号日 {current_volume / max(signal_day_volume, 1):.1f} 倍",
            }

    return {"action": "hold", "reason": ""}


# ── 内部辅助函数 ──────────────────────────────────────────────────────────


def _count_limit_up(db: Session, trade_date: str) -> int:
    """统计当日涨停家数。"""
    return db.scalar(
        select(func.count(func.distinct(LimitUpBoard.code)))
        .where(
            LimitUpBoard.trade_date == trade_date,
            LimitUpBoard.limit_type == "U",
        )
    ) or 0


def _count_limit_down(db: Session, trade_date: str) -> int:
    """统计当日跌停家数。

    使用两种方式：
      1. limit_up_board 中 limit_type = 'D'
      2. daily_kbars 中 pct_chg <= -9.8
    以较大值为准。
    """
    from_db = db.scalar(
        select(func.count(func.distinct(LimitUpBoard.code)))
        .where(
            LimitUpBoard.trade_date == trade_date,
            LimitUpBoard.limit_type == "D",
        )
    ) or 0

    from_kbar = db.scalar(
        select(func.count(DailyKbar.code))
        .where(
            DailyKbar.trade_date == trade_date,
            DailyKbar.pct_chg <= -9.8,
        )
    ) or 0

    return max(from_db, from_kbar)


def _max_limit_up_consecutive(db: Session, trade_date: str) -> int:
    """获取当日最大连板高度。

    使用 limit_up_board.limit_times 字段（表示个股连续涨停天数）。
    """
    return db.scalar(
        select(func.max(LimitUpBoard.limit_times))
        .where(
            LimitUpBoard.trade_date == trade_date,
            LimitUpBoard.limit_type == "U",
        )
    ) or 0


def _compute_promotion_rate(db: Session, trade_date: str) -> float:
    """计算当日连板晋级率。

    晋级率 = 今日连板数（今日涨停且昨日也涨停） / 昨日涨停总数。

    Returns:
        0~1 的比值
    """
    # 昨日涨停总数
    prev_date = _prev_trade_date(db, trade_date)
    if not prev_date:
        return 0.0

    yesterday_limit_up = db.scalar(
        select(func.count(func.distinct(LimitUpBoard.code)))
        .where(
            LimitUpBoard.trade_date == prev_date,
            LimitUpBoard.limit_type == "U",
        )
    ) or 1  # 避免除零

    # 获取昨日涨停的股票代码集合
    yesterday_codes = set(
        db.scalars(
            select(LimitUpBoard.code).where(
                LimitUpBoard.trade_date == prev_date,
                LimitUpBoard.limit_type == "U",
            )
        ).all()
    )

    if not yesterday_codes:
        return 0.0

    # 今日涨停且昨日也涨停（连板）的股票数
    today_codes = set(
        db.scalars(
            select(LimitUpBoard.code).where(
                LimitUpBoard.trade_date == trade_date,
                LimitUpBoard.limit_type == "U",
            )
        ).all()
    )

    carry_over = len(yesterday_codes & today_codes)
    return carry_over / yesterday_limit_up if yesterday_limit_up > 0 else 0.0


def _count_tier_completeness(db: Session, trade_date: str) -> bool:
    """检查梯队完整性：2板≥MIN_TIER_2_BOARD 且 3板≥MIN_TIER_3_BOARD。"""
    tier_2_count = db.scalar(
        select(func.count(func.distinct(LimitUpBoard.code)))
        .where(
            LimitUpBoard.trade_date == trade_date,
            LimitUpBoard.limit_type == "U",
            LimitUpBoard.limit_times >= 2,
        )
    ) or 0

    tier_3_count = db.scalar(
        select(func.count(func.distinct(LimitUpBoard.code)))
        .where(
            LimitUpBoard.trade_date == trade_date,
            LimitUpBoard.limit_type == "U",
            LimitUpBoard.limit_times >= 3,
        )
    ) or 0

    return tier_2_count >= _MIN_TIER_2_BOARD and tier_3_count >= _MIN_TIER_3_BOARD


def _prev_trade_date(db: Session, trade_date: str) -> str | None:
    """获取上一个交易日。"""
    row = db.execute(
        select(DailyKbar.trade_date)
        .where(DailyKbar.trade_date < trade_date)
        .order_by(DailyKbar.trade_date.desc())
        .limit(1)
    ).scalar()
    return str(row) if row else None
