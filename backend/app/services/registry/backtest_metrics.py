"""策略三窗回测指标 artifact 读取器（只读、按 mtime 进程内缓存）。

artifact 位置：`app/policies/strategy_backtest_metrics.json`
生成方式：`scripts/export_strategy_backtest_metrics.py`（源数据为
`scripts/research_entry_rule_panel.py --single-skill` 的三窗回测结果）。

设计取舍：这些指标来自 path A 诊断回测（不是 live_forward 发布证据），因此**不塞进**
`performance`（canonical_backtest 画像语义），而是作为独立字段 `backtest_returns`
附在策略清单项上，前端用独立区块 + 口径说明展示，避免两种口径混算。
文件缺失或损坏时返回 None（页面降级为只显示原有画像），绝不让页面 500。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ARTIFACT = Path(__file__).resolve().parents[2] / "policies" / "strategy_backtest_metrics.json"
_CACHE: dict[str, Any] = {"key": None, "payload": None}


def load_backtest_metrics(path: Path | None = None) -> dict[str, Any] | None:
    """读取 artifact；不存在/不可解析时返回 None（调用方降级处理）。"""
    target = Path(path) if path is not None else _ARTIFACT
    try:
        stat = target.stat()
    except OSError:
        return None
    key = (str(target), stat.st_mtime_ns)
    if _CACHE["key"] == key:
        return _CACHE["payload"]
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("策略三窗回测指标 artifact 读取失败：%s", target)
        return None
    if not isinstance(payload, dict):
        logger.warning("策略三窗回测指标 artifact 结构异常（非 object）：%s", target)
        return None
    _CACHE["key"] = key
    _CACHE["payload"] = payload
    return payload


def backtest_returns_for(
    skill_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """取单个技能的三窗回测指标（含组合基线与口径说明）；无数据返回 None。"""
    data = payload if payload is not None else load_backtest_metrics()
    if not data:
        return None
    skill = (data.get("skills") or {}).get(str(skill_id))
    if not isinstance(skill, dict):
        return None
    return {
        "windows": skill.get("windows") or {},
        "windows_with_trades": skill.get("windows_with_trades"),
        "passes": skill.get("passes"),
        "avg_net_pct_mean": skill.get("avg_net_pct_mean"),
        "all_windows_positive": bool(skill.get("all_windows_positive")),
        "any_window_positive": bool(skill.get("any_window_positive")),
        "combined": data.get("combined") or {},
        # 冠军（当前生产默认 + 出场策略）的全管线三窗结果与逐技能贡献（L-129 发布口径）。
        # 仅透传数据，不参与任何判定；前端未消费时无副作用。
        "champion": data.get("champion") or {},
        "champion_contributions": (data.get("champion_contributions") or {}).get(str(skill_id)) or {},
        "caliber": data.get("caliber") or {},
        "windows_meta": data.get("windows") or {},
        "generated_at": data.get("generated_at"),
        "schema_version": data.get("schema_version"),
    }


__all__ = ["load_backtest_metrics", "backtest_returns_for"]
