"""推荐运行的数据源模式常量与默认预设配置。

任务 7.4（需求 6.1）落地：把散落在 `workbench_service.py` 里的
`DATA_SOURCE_*` / `ALLOWED_DATA_SOURCES` / `DEFAULT_RECOMMENDATION_DATA_SOURCE` /
`DEFAULT_PRESETS` 收口到本模块，供编排层（`workbench_service.py`）与
`registry/presets.py` 共用，避免二者相互 import 造成循环依赖。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

DATA_SOURCE_LOCAL_POOL = "local_pool"
DATA_SOURCE_LOCAL_POOL_PREFERRED = "local_pool_preferred"
DATA_SOURCE_LOCAL_POOL_REALTIME = "local_pool_realtime"
DATA_SOURCE_REALTIME = "realtime"
DATA_SOURCE_AUTO = "auto"
DEFAULT_RECOMMENDATION_DATA_SOURCE = DATA_SOURCE_AUTO
ALLOWED_DATA_SOURCES = {
    DATA_SOURCE_LOCAL_POOL,
    DATA_SOURCE_LOCAL_POOL_PREFERRED,
    DATA_SOURCE_LOCAL_POOL_REALTIME,
    DATA_SOURCE_REALTIME,
    DATA_SOURCE_AUTO,
}

DEFAULT_PRESETS: list[dict[str, Any]] = [
    {
        "preset_id": "preset_formal_opening",
        "name": "盘前组合",
        "description": "盘前默认组合，强调主线、技术确认和风险平衡。",
        "data_source": DEFAULT_RECOMMENDATION_DATA_SOURCE,
        "market_scope": "all",
        "market_scope_value": None,
        "risk_template": "balanced",
        "score_line": 72.0,
        "output_count": 6,
        "hide_recent_repeats": True,
        "dedup_same_day": True,
        "skip_aux_refresh": False,
        "skill_groups": ["formal_opening"],
    },
    {
        "preset_id": "preset_formal_trend_core",
        "name": "趋势龙头组合",
        "description": "适合主线趋势票，偏重龙头和大局观。",
        "market_scope": "all",
        "market_scope_value": None,
        "risk_template": "aggressive",
        "score_line": 70.0,
        "output_count": 5,
        "hide_recent_repeats": True,
        "dedup_same_day": True,
        "skip_aux_refresh": False,
        "skill_groups": ["formal_trend_core"],
    },
    {
        "preset_id": "preset_experiment_dsa",
        "name": "基础短线策略单 Skill 验证",
        "description": "单独验证技术评分 Skill 的辨识度。",
        "market_scope": "all",
        "market_scope_value": None,
        "risk_template": "balanced",
        "score_line": 68.0,
        "output_count": 6,
        "hide_recent_repeats": False,
        "skip_aux_refresh": True,
        "skill_groups": ["experiment_single_factor"],
    },
    {
        "preset_id": "preset_experiment_high_range_breakout",
        "name": "阶段新高突破单策略",
        "description": "单独验证阶段高点放量突破动量规则。",
        "market_scope": "all",
        "market_scope_value": None,
        "risk_template": "balanced",
        "score_line": 66.0,
        "output_count": 5,
        "hide_recent_repeats": False,
        "skip_aux_refresh": True,
        "skill_groups": ["experiment_high_range_breakout"],
    },
    {
        "preset_id": "preset_experiment_limit_up_reseal_relay",
        "name": "涨停回封接力单策略",
        "description": "单独验证涨停开板回封后的短线接力规则。",
        "market_scope": "all",
        "market_scope_value": None,
        "risk_template": "aggressive",
        "score_line": 64.0,
        "output_count": 5,
        "hide_recent_repeats": False,
        "skip_aux_refresh": True,
        "skill_groups": ["experiment_limit_up_reseal_relay"],
    },
    {
        "preset_id": "preset_experiment_trend_pullback",
        "name": "强趋势缩量回踩单策略",
        "description": "单独验证多头趋势内缩量回踩 MA5/MA10 的低吸规则。",
        "market_scope": "all",
        "market_scope_value": None,
        "risk_template": "balanced",
        "score_line": 63.0,
        "output_count": 5,
        "hide_recent_repeats": False,
        "skip_aux_refresh": True,
        "skill_groups": ["experiment_trend_pullback"],
    },
    {
        "preset_id": "preset_experiment_sector_relative_strength",
        "name": "主线相对强度单策略",
        "description": "单独验证行业动量与主线龙头相对强度规则。",
        "market_scope": "all",
        "market_scope_value": None,
        "risk_template": "balanced",
        "score_line": 66.0,
        "output_count": 5,
        "hide_recent_repeats": False,
        "skip_aux_refresh": True,
        "skill_groups": ["experiment_sector_relative_strength"],
    },
]


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
