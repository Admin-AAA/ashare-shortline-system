"""数据网关拓扑投影（配置页「数据源管理」）：数据类型 → 有序 provider → 开关 + 熔断状态。

* 网关实例（quote.realtime / bar.minute / ...）的 provider 与顺序直接取自 ``gateway()._providers``；
  ``gw_call`` 包装的数据类型取自 ``wrap.PROVIDERS`` + 数据源注册表的配置顺序。
* 两层开关，叠加生效：
  - 源总开关 = ``data_source_config`` 的 (source, source_type) 行（``provider_enabled``），
    可被多个数据类型共用（如 xiaoshi/quote），关掉后所有数据类型都跳过该源；
  - 数据项级开关 = ``data_gateway_item_switch`` 的 (数据项键, provider) 行（``item_enabled``），
    只在这一列跳过该源。配置页的禁用按钮写的是数据项级开关（Tushare Pro 路由列除外）。
* 熔断只读 live 命名空间（后端 + Prefect worker）；research 脚本的熔断不影响实盘，不展示。
"""
from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

from app.core.shared_breaker import LIVE_NAMESPACE

LABELS: dict[str, str] = {
    "tushare_rt_k": "Promax rt_k", "sina_realtime": "新浪", "tencent_qt": "腾讯",
    "tushare_rt_min": "QuantData rt_min", "xiaoshi_snapshot": "小石快照",
    "xiaoshi_min1": "小石 1 分钟", "baostock": "Baostock", "tushare_stk_mins": "Tushare stk_mins",
    "sina_kline": "新浪分钟", "tencent_mkline": "腾讯分钟", "akshare": "AKShare",
    "eastmoney": "东方财富", "cninfo": "巨潮", "cninfo_search": "巨潮全文检索",
    "xiaoshi_alt": "小石另类数据", "xiaoshi_quote": "小石", "xiaoshi_kline": "小石",
    "promax": "Promax", "quantdata": "QuantData", "teajoin": "Teajoin",
}
# Tushare 路由下游的接口：这些 provider 还受 tushare:<pro>:<api> 路由熔断约束。
ROUTER_APIS: dict[str, tuple[str, ...]] = {
    "tushare_rt_k": ("rt_k",), "tushare_rt_min": ("rt_min",), "tushare_stk_mins": ("stk_mins",),
}
PRO_PROVIDERS = ("promax", "quantdata", "teajoin")
_SAMPLE_QUERIES = ({}, {"freq": "1min"}, {"freq": "5min"})


def _gateway_datasets() -> list[dict[str, Any]]:
    from app.services.datasource import akshare_backup, bar_minute, quote_realtime, sina_realtime

    return [
        {"key": "quote.realtime", "label": "实时行情", "mode": "fill", "gw": quote_realtime.gateway()},
        {"key": "quote.realtime_frame", "label": "实时行情（批量帧）", "mode": "first", "gw": sina_realtime.gateway()},
        {"key": "bar.minute", "label": "分钟 K 线", "mode": "first", "gw": bar_minute.gateway()},
        {"key": akshare_backup.BACKUP_ITEM_KEY, "label": "Tushare 灾备", "mode": "first", "gw": akshare_backup.backup_gateway(),
         "breaker_prefix": "tushare.", "note": "Tushare 路由全部失败时的兜底（日线/交易日历等）"},
    ]


def _registry() -> dict[str, list[str]]:
    from app.core.config import get_settings
    from app.services.data_sources import registry_sources

    return registry_sources(get_settings())


def _wrap_datasets(reg: dict[str, list[str]]) -> list[dict[str, Any]]:
    def names(st: str, fmt: str) -> list[str]:
        return [("eastmoney" if s == "eastmoney" else fmt.format(s)) for s in reg.get(st, [])]

    return [
        {"key": "quote.daily", "label": "日线快照", "providers": names("quote", "{}_quote")},
        {"key": "bar.daily", "label": "日 K 线", "providers": names("kline", "{}_kline")},
        {"key": "aux.limit_up", "label": "涨停板", "providers": names("limit_up", "{}_limit_up")},
        {"key": "aux.concept_sector", "label": "概念板块", "providers": names("concept_sector", "{}_concept_sector")},
        {"key": "aux.capital_flow", "label": "资金流", "providers": names("capital_flow", "{}_capital_flow")},
        {"key": "aux.dragon_tiger", "label": "龙虎榜", "providers": names("dragon_tiger", "{}_dragon_tiger")},
        {"key": "aux.news", "label": "新闻公告", "providers": names("news", "{}_news")},
        {"key": "ann.list", "label": "公告补数", "providers": ["eastmoney", "cninfo", "xiaoshi_alt"]},
        {"key": "ann.search.cninfo", "label": "公告全文检索", "providers": ["cninfo_search"]},
        {"key": "xiaoshi.alt", "label": "另类数据", "providers": ["xiaoshi_alt"]},
        {"key": "fina_indicator", "label": "财务指标补数", "providers": ["promax"]},
    ]


def _breaker_rows() -> list[tuple[str, float, str]]:
    from app.core import shared_breaker as sb

    try:
        conn = sb._conn()
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT key, open_until, reason FROM breaker_state WHERE key LIKE ?", (f"{LIVE_NAMESPACE}:%",)
        ).fetchall()
        conn.close()
    except Exception:  # noqa: BLE001 - 熔断库不可用时按全部关闭展示
        return []
    return [(str(k)[len(LIVE_NAMESPACE) + 1:], float(ou), str(r or "")) for k, ou, r in rows]


def _breaker_view(rows: Iterable[tuple[str, float, str]], match) -> dict[str, Any]:
    """open: 冷却中拒绝请求；half_open: 冷却已过，下一次请求做探测；closed: 无记录。"""
    now = time.time()
    hits = [{"key": k, "open_until": ou, "reason": r, "state": "open" if ou > now else "half_open"}
            for k, ou, r in rows if match(k)]
    state = "open" if any(h["state"] == "open" for h in hits) else ("half_open" if hits else "closed")
    return {"state": state, "items": sorted(hits, key=lambda h: -h["open_until"])}


def _disabled_items(db: Any) -> set[tuple[str, str]]:
    from sqlalchemy import select

    from app.models.tables import DataGatewayItemSwitch

    try:
        return {(str(r.dataset_key), str(r.provider))
                for r in db.scalars(select(DataGatewayItemSwitch)).all() if not bool(r.enabled)}
    except Exception:  # noqa: BLE001 - 表还没迁移：按全部启用展示，不让整页挂掉
        db.rollback()
        return set()


def build_topology(db: Any) -> dict[str, Any]:
    from sqlalchemy import select

    from app.models.tables import DataSourceConfig
    from app.services.datasource import wrap
    from app.services.datasource.gateway import is_cutover

    rows = _breaker_rows()
    cfg = {(r.source, r.source_type): bool(r.enabled) for r in db.scalars(select(DataSourceConfig)).all()}
    off_items = _disabled_items(db)

    def switch(keys: Iterable[tuple[str, str]]) -> list[dict[str, Any]]:
        return [{"source": s, "source_type": t, "enabled": cfg.get((s, t), True)} for s, t in keys]

    def item(dataset_key: str, name: str, switches: list[dict[str, Any]]) -> dict[str, Any]:
        # source_enabled: 源总开关（可能被多个数据类型共用）；item_enabled: 只管这一列的数据项级开关。
        return {"item_key": dataset_key, "item_enabled": (dataset_key, name) not in off_items,
                "source_enabled": all(s["enabled"] for s in switches), "item_switchable": True}

    def router_match(apis: tuple[str, ...]):
        return lambda k: any(k == f"tushare:{p}:{a}" for p in PRO_PROVIDERS for a in apis)

    datasets: list[dict[str, Any]] = []
    for ds in _gateway_datasets():
        prefix = ds.get("breaker_prefix")
        provs = []
        for p in ds["gw"]._providers:
            name = p.name
            upstream = tuple(getattr(p, "upstream_breaker_keys", ()) or ())
            own = (lambda k, n=name: k.startswith(f"gw:{n}:{prefix}")) if prefix else (
                lambda k, n=name, d=ds["key"]: k == f"gw:{n}:{d}")
            rapi = router_match(ROUTER_APIS.get(name, ()))
            # 灾备按接口选源，supports 依赖具体 api，一律视为在链上。
            active = bool(prefix) or any(p.supports(ds["key"], q) for q in _SAMPLE_QUERIES)
            sw = switch(p.config_keys)
            provs.append({
                "name": name, "label": LABELS.get(name, name), "switches": sw,
                **item(ds["gw"].item_key_for(ds["key"]), name, sw),
                "active": active, "inactive_reason": None if active else "备用源，默认不在回退链上（需单独开启）",
                "breaker": _breaker_view(rows, lambda k, o=own, r=rapi: o(k) or k in upstream or r(k)),
            })
        datasets.append({"key": ds["key"], "label": ds["label"], "mode": ds["mode"], "routed": True,
                         "note": ds.get("note"), "providers": provs})

    for ds in _wrap_datasets(_registry()):
        routed = is_cutover(ds["key"])
        provs = []
        for name in ds["providers"]:
            spec = wrap.PROVIDERS.get(name, wrap.ProviderSpec((), 0))
            sw = switch(spec.config_keys)
            provs.append({
                "name": name, "label": LABELS.get(name, name.split("_")[0].capitalize()),
                "switches": sw, **item(ds["key"], name, sw), "active": True, "inactive_reason": None,
                "breaker": _breaker_view(rows, lambda k, n=name, d=ds["key"]: k == f"gw:{n}:{d}"),
            })
        datasets.append({"key": ds["key"], "label": ds["label"], "mode": "chain", "routed": routed,
                         "note": None if routed else "未切到网关：开关生效，熔断由各源自身 / Tushare 路由负责",
                         "providers": provs})

    pro = []
    try:
        from app.services.data_sources import _provider_health_rows

        prio = {str(r["provider_name"]): int(r.get("priority") or 0) for r in _provider_health_rows()}
    except Exception:  # noqa: BLE001 - 取不到优先级时按默认顺序
        prio = {}
    for name in sorted(PRO_PROVIDERS, key=lambda n: prio.get(n, 99)):
        sw = switch([(name, "tushare_provider")])
        # 路由这一列就是 Tushare Pro 源本身（覆盖全部 Tushare 接口），没有更细的数据项：
        # 这里的按钮操作的是源总开关，会影响用到该源的其他数据类型（前端标「总开关」）。
        pro.append({
            "name": name, "label": LABELS[name], "switches": sw,
            "item_key": "tushare.*", "item_enabled": True, "source_enabled": all(s["enabled"] for s in sw),
            "item_switchable": False, "active": True, "inactive_reason": None,
            "breaker": _breaker_view(rows, lambda k, n=name: k.startswith(f"tushare:{n}:")),
        })
    datasets.append({"key": "tushare.*", "label": "Tushare Pro 路由", "mode": "router", "routed": True,
                     "note": "日线/基本面/资金流等全部 Tushare 接口；熔断按接口粒度", "providers": pro})
    return {"namespace": LIVE_NAMESPACE, "generated_at": time.time(), "datasets": datasets}


def gateway_item_keys() -> set[tuple[str, str]]:
    """「数据源管理」里可单独启停的 (数据项键, provider)；Tushare Pro 路由列不在内（它只有总开关）。"""
    keys: set[tuple[str, str]] = set()
    for ds in _gateway_datasets():
        item_key = ds["gw"].item_key_for(ds["key"])
        keys.update((item_key, p.name) for p in ds["gw"]._providers)
    for ds in _wrap_datasets(_registry()):
        keys.update((ds["key"], name) for name in ds["providers"])
    return keys


def gateway_config_keys(db: Any = None) -> set[tuple[str, str]]:
    """网关 provider 声明的全部开关键（数据源注册表孤儿清理须豁免，否则禁用行会被秒删）。"""
    from app.services.datasource import wrap

    keys: set[tuple[str, str]] = set()
    for ds in _gateway_datasets():
        for p in ds["gw"]._providers:
            keys.update(tuple(k) for k in p.config_keys)
    for spec in wrap.PROVIDERS.values():
        keys.update(tuple(k) for k in spec.config_keys)
    return keys
