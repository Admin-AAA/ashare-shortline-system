"""推荐运行的数据源模式常量。

任务 7.4（需求 6.1）落地：把散落在 `workbench_service.py` 里的
`DATA_SOURCE_*` / `ALLOWED_DATA_SOURCES` / `DEFAULT_RECOMMENDATION_DATA_SOURCE`
收口到本模块，供编排层（`workbench_service.py`）与各注册表模块共用。

预设（preset）管理层已于 2026-11-15 退役：正式推荐改为单策略独立 run，
运行口径由 `standalone_strategies.py` 定义，不再有默认预设。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

DATA_SOURCE_LOCAL_POOL = "local_pool"
DATA_SOURCE_LOCAL_POOL_PREFERRED = "local_pool_preferred"
DATA_SOURCE_LOCAL_POOL_REALTIME = "local_pool_realtime"
DATA_SOURCE_REALTIME = "realtime"
DATA_SOURCE_AUTO = "auto"
# 普通运行默认走“本地历史 + 实时快照”：历史日K/技术指标读本地池，
# 当日行情拼 ``realtime_quote`` 实时快照（见
# ``market_pool.get_market_universe_from_pool_with_realtime``）。
# 回测/历史补跑经 ``trade_date_override`` 走本地历史快照，盘后流水线则显式
# 指定 ``local_pool``，二者都不受影响。
DEFAULT_RECOMMENDATION_DATA_SOURCE = DATA_SOURCE_LOCAL_POOL_REALTIME
ALLOWED_DATA_SOURCES = {
    DATA_SOURCE_LOCAL_POOL,
    DATA_SOURCE_LOCAL_POOL_PREFERRED,
    DATA_SOURCE_LOCAL_POOL_REALTIME,
    DATA_SOURCE_REALTIME,
    DATA_SOURCE_AUTO,
}


def normalize_data_source(value: Any) -> str:
    data_source = str(value or DEFAULT_RECOMMENDATION_DATA_SOURCE)
    if data_source not in ALLOWED_DATA_SOURCES:
        raise ValueError("运行数据仅支持 auto、local_pool、local_pool_preferred、local_pool_realtime 或 realtime")
    if data_source == DATA_SOURCE_AUTO:
        now = datetime.now()
        # 工作日 09:15-15:00 为盘中，用实时模式
        if now.weekday() < 5 and (now.hour > 9 or (now.hour == 9 and now.minute >= 15)) and now.hour < 15:
            return DATA_SOURCE_LOCAL_POOL_REALTIME
        return DATA_SOURCE_LOCAL_POOL_PREFERRED
    return data_source
