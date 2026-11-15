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
* **2026-11-15 追加（owner 决定「以后也不会接入」）**：本地历史存档
  （`data/xiaoshi_min1` 4.6G、`data/xiaoshi_min_all` 525M、`data/xiaoshi_daily` 31M）已删除，
  专用同步/回填脚本（`sync_xiaoshi_min_all.py`、`backfill_xiaoshi_announcements.py`、
  `ingest_event_timeline.py`）与两项文件巡检已移除，launchd 定时作业已卸载。
  **本模块的条目从此是「永久退役」，不再有复活路径**；如需重接，按新源重新接入并单独立项。
* 适配器模块（`xiaoshi_client` / `xiaoshi_minute_source` / `xiaoshi_alt_data` /
  `xiaoshi_market_source` / `datasource/xiaoshi_ann`）暂留但已不在任何运行时链路上，
  仅作为「读本地表的历史回退」（如 `auxiliary/application` 的 share_float 只读本地库）依赖，
  属只读残留，不会发起网络请求。
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
