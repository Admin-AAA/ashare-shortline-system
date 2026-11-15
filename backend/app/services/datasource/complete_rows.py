"""按数据中心 provider 优先级链取「全市场单日」数据，把「半量」当换源信号。

背景
----
网关的 fallback 只在**异常/空结果**时换源；上游「只返回半个市场」（例如
margin_detail 某日只发布沪市 2019 行、正常 4489 行）看起来是一次成功请求，
选路链就停在第一家。各补数脚本因此各自硬编码一个备用源 ——
``margin_detail_fill`` 曾只认东财一家，既绕开 ``data_source_config`` 的
启停/优先级、``routing_table.json`` 的链序和熔断限流，也拿不到多源互补的行。

数据中心其实已为这个场景准备了 ``TushareProviderRouter.request_pinned``
（docstring：「主源返回了可疑的截断结果，改问另一家核实」）。本模块把它串成
一条完整链：按 router 排好的顺序逐个问，按主键合并去重，达到预期行数即停，
否则穷尽所有启用源后返回最完整的合并结果。

设计约束
--------
* 不新增凭据、不改路由表，完全复用现有 enabled/priority/leaders/熔断。
* 先到的源优先（链序靠前 = 数据中心认为更可信），后到只补它没有的主键。
* 「达标」由调用方给 expected_rows；给 0 表示无法判断完整性，会问完所有源。
* 单源失败不影响其它源；所有失败记进 errors，便于 provenance/巡检诚实标注。
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

logger = logging.getLogger(__name__)


def envelope_rows(env: Any) -> list[dict[str, Any]]:
    """把 Tushare-compatible envelope {data:{fields,items}} 拆成 dict 行列表。"""
    data = (env or {}).get("data") or {}
    fields = list(data.get("fields") or [])
    items = list(data.get("items") or [])
    return [dict(zip(fields, row)) for row in items if len(row) == len(fields)]


def ordered_provider_names(api_name: str, router: Any) -> list[str]:
    """取数据中心实际会用的 provider 链（已应用启停、优先级、路由表、leaders）。"""
    try:
        ordered = router._ordered_providers(api_name)
        return [p.name for p in ordered]
    except Exception:  # noqa: BLE001 - 私有排序接口不可用时退化为全量 provider
        logger.debug("ordered_providers unavailable, falling back to provider map", exc_info=True)
        return [p.name for p in getattr(router, "_providers", {}).values()]


def fetch_complete_trade_date(
    api_name: str,
    trade_date: str,
    *,
    key: str = "ts_code",
    expected_rows: int = 0,
    complete_ratio: float = 0.95,
    router: Any = None,
    exclude: Sequence[str] = (),
    extra_fetchers: Sequence[tuple[str, Callable[[str], Any]]] = (),
) -> dict[str, Any]:
    """返回 ``{rows, by_source, attempted, errors, complete, expected_rows}``。

    ``rows`` 是各源原始 dict 行的去重合并结果（不做业务字段转换）。
    ``extra_fetchers`` 为链尾补充源（东财等网站接口），其返回值应已是 dict 行列表。
    """
    if router is None:
        from app.services.tushare_client import _build_provider_router

        router, _ = _build_provider_router()

    target = max(0, int(expected_rows or 0))
    threshold = int(target * complete_ratio) if target else 0
    merged: dict[str, dict[str, Any]] = {}
    by_source: dict[str, int] = {}
    attempted: list[str] = []
    errors: list[str] = []
    params = {"trade_date": str(trade_date).replace("-", "")}

    def absorb(name: str, rows: Any) -> None:
        added = 0
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            k = str(row.get(key) or row.get("code") or "").split(".")[0]
            if not k or k in merged:
                continue
            merged[k] = row
            added += 1
        by_source[name] = added

    for name in ordered_provider_names(api_name, router):
        if name in exclude:
            continue
        attempted.append(name)
        try:
            env = router.request_pinned(name, api_name, params=params, fields="")
            absorb(name, envelope_rows(env))
        except Exception as exc:  # noqa: BLE001 - 单源失败继续问下一家
            errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:120]}")
            by_source[name] = 0
        if threshold and len(merged) >= threshold:
            break

    for name, fn in extra_fetchers:
        attempted.append(name)
        try:
            absorb(name, fn(trade_date))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:120]}")
            by_source[name] = 0
        if threshold and len(merged) >= threshold:
            break

    return {
        "rows": list(merged.values()),
        "by_source": by_source,
        "attempted": attempted,
        "errors": errors,
        "complete": bool(threshold and len(merged) >= threshold),
        "expected_rows": target,
    }
