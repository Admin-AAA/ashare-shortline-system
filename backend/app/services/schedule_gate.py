# 定时作业放行判定：按计划触发时刻判断是否休市、是否错过有效时段
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Callable

CST = timezone(timedelta(hours=8))
# 收盘时刻。计划在收盘前触发的作业（盘前建监控、盘前曝光补跑、安全网、盘中轮询）
# 只在当日交易时段内有意义；机器休眠或重启后 Prefect 会把错过的计划一次性补跑，
# 收盘后才执行的这类作业只会和盘后作业抢资源，必须跳过。
SESSION_CLOSE = time(15, 0)


def schedule_skip_reason(
    scheduled_at: datetime,
    now: datetime,
    is_open_trade_date: Callable[[date], bool],
) -> str | None:
    """返回跳过原因；返回 None 表示放行。

    scheduled_at/now 必须带时区。交易日按计划触发时刻的北京日期判定，
    补跑跨过零点时也按原计划日判断。
    """
    scheduled_cst = scheduled_at.astimezone(CST)
    now_cst = now.astimezone(CST)
    plan_day = scheduled_cst.date()
    if not is_open_trade_date(plan_day):
        return f"非交易日跳过：{plan_day.isoformat()} 交易所休市"
    if scheduled_cst.time() < SESSION_CLOSE:
        deadline = datetime.combine(plan_day, SESSION_CLOSE, tzinfo=CST)
        if now_cst >= deadline:
            return (
                f"迟到超时跳过：计划 {scheduled_cst:%m-%d %H:%M} 触发，"
                f"实际 {now_cst:%m-%d %H:%M} 才开始执行，已过当日 15:00 收盘，"
                "收盘前的作业不再补跑"
            )
    return None
