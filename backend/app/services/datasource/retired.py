"""已退役的上游源：配置里仍可能残留，但系统不再把它当作可用数据源。

背景（2026-11-15）：小石（`api.shizixi.com`）域名解析到不可路由的 `28.0.0.137`，
`/api/v3/manifest`、`/api/v3/history/catalog` 全部连不通，本地分钟缓存停在 2026-09-18，
`data/xiaoshi_min_all/sync.log` 每日 `fatal`。它在 `bar.minute` 链首空耗 6~16 秒，
并发补数时撞网关 120s deadline 成批放弃，直接把分钟线日更拖成「腰斩日」（见 L-200）。

处理原则
--------
* **一处判定，处处生效**：所有对外暴露「有哪些数据源」的入口（数据源管理页、网关拓扑、
  数据集开关、能力探测、同步任务）都过 :func:`is_retired` / :func:`provider_retired`，
  不再各写一份 if。
* **不删代码、不删数据**：适配器模块与本地 parquet 存档保留（恢复订阅时可原地复活），
  但任何运行时链路都不会再调到它们。
* 退役是数据源级别的，不是数据集级别的：小石同时供 quote/kline/alt 三类能力，
  按源退役才能保证不会从另一条链路漏出来。
"""
from __future__ import annotations

# source -> 退役原因（写进管理页/日志，避免以后有人当成 bug 又接回去）
RETIRED_SOURCES: dict[str, str] = {
    "xiaoshi": "2026-11-15 退役：api.shizixi.com 解析到不可路由的 28.0.0.137，2026-09-18 起无成功取数",
}


def is_retired(source: str) -> bool:
    """该源是否已退役（入参是配置/注册表里的 source 名，如 ``xiaoshi``）。"""
    return str(source or "").strip().lower() in RETIRED_SOURCES


def provider_retired(provider: str) -> bool:
    """provider 名是否属于退役源。

    网关里的 provider 名是 ``<source>_<能力>``（xiaoshi_min1 / xiaoshi_kline /
    xiaoshi_quote / xiaoshi_alt / xiaoshi_snapshot），所以按前缀判定，避免逐个列名字。
    """
    name = str(provider or "").strip().lower()
    return any(name == src or name.startswith(f"{src}_") for src in RETIRED_SOURCES)


def retired_note(source: str) -> str:
    return RETIRED_SOURCES.get(str(source or "").strip().lower(), "")


def filter_active(sources: list[str]) -> list[str]:
    """从配置顺序里剔掉退役源（保持其余顺序不变）。"""
    return [s for s in sources if not is_retired(s)]
