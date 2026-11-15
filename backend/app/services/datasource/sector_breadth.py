"""Dataset ``aux.sector_breadth``：板块广度（同花顺 ``market_data_block`` 基础数据）。

现状缺口：系统里 ``industry_positive_excess20_count`` / ``sector_ladder_completeness_score`` 都是**用个股
快照按行业分组自己算**的（``market_source/snapshot.py``），``concept_sector`` 表只有成员数和热度，
没有任何「板块级上涨/下跌/涨停家数」字段。本数据集补的就是这一层：上游直接给板块聚合后的广度。

设计取舍（实测，2026-11-15）：

* ``market_block("URFI")`` 一次返回 630 个板块目录（代码+名称），足够替代 ths_industry/ths_concept；
  目录缓存 1 小时，避免每次扫描都先查一次。
* ``market_data_block`` 只能**单板块**查询，630 个全量扫一遍 ≈ 38s（官方限频 20ms/次），盘中不可用。
  因此默认只查传入的 ``block_codes``；不传时退化为行业板块全量（90 个，≈5.5s），并受
  ``max_blocks`` 硬上限保护。
* rows 是 ``{block_code: row}``，网关 first 模式，key 是板块代码。

字段口径来自实测返回：成交量/总金额/领涨股/涨停家数/跌停家数/上涨家数/下跌家数/板块流通市值/板块总市值。
"""
from __future__ import annotations

import time
from typing import Any, Mapping

from app.services.datasource import ths_client
from app.services.datasource.gateway import DatasetGateway, DataSourceError, ErrorKind

DATASET = "aux.sector_breadth"
DEFAULT_MAX_BLOCKS = 120
_INDEX_TTL_SECONDS = 3600.0
_INDEX_CACHE: dict[str, tuple[float, list[dict[str, str]]]] = {}


def _load_index(kind: str) -> list[dict[str, str]]:
    """板块目录：``market_block("URFI")`` → [{code, name}]，带 1 小时缓存。"""
    hit = _INDEX_CACHE.get(kind)
    if hit and time.time() - hit[0] < _INDEX_TTL_SECONDS:
        return hit[1]
    if kind == "industry":
        rows = ths_client.call("ths_industry", dataset=DATASET)
    elif kind == "concept":
        rows = ths_client.call("ths_concept", dataset=DATASET)
    else:
        rows = ths_client.call("market_block", "URFI", dataset=DATASET)
    out = [
        {"code": str(r.get("代码") or r.get("code") or ""), "name": str(r.get("名称") or r.get("name") or "")}
        for r in rows
        if str(r.get("代码") or r.get("code") or "").strip()
    ]
    _INDEX_CACHE[kind] = (time.time(), out)
    return out


def _row(block_code: str, raw: Mapping[str, Any], name: str = "", trade_date: str = "") -> dict[str, Any]:
    code = str(raw.get("代码") or block_code)
    return {
        "block_code": code,
        "block_name": str(raw.get("名称") or name or ""),
        "trade_date": trade_date,  # 上游基础数据不带交易日，由 fetch 按 latest_allowed_quote_date 补齐
        "up_count": ths_client.clean_int(raw.get("上涨家数")),
        "down_count": ths_client.clean_int(raw.get("下跌家数")),
        "limit_up_count": ths_client.clean_int(raw.get("涨停家数")),
        "limit_down_count": ths_client.clean_int(raw.get("跌停家数")),
        "leading_code": str(raw.get("领涨股") or "") or None,
        "volume": ths_client.clean_number(raw.get("成交量")),
        "amount": ths_client.clean_number(raw.get("总金额")),
        "float_mv": ths_client.clean_number(raw.get("板块流通市值")),
        "total_mv": ths_client.clean_number(raw.get("板块总市值")),
        "source": "ths_block",
    }


class ThsBlockBreadthProvider:
    """同花顺板块广度（``aux.sector_breadth``）。"""

    name = "ths_block"
    config_keys = (("ths", "concept_sector"),)

    def supports(self, dataset: str, query: Mapping[str, Any]) -> bool:
        return dataset == DATASET

    def fetch(self, dataset: str, query: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        codes = [str(c).strip() for c in (query.get("block_codes") or []) if str(c).strip()]
        names: dict[str, str] = {}
        if not codes:
            kind = str(query.get("kind") or "industry")
            index = _load_index(kind)
            cap = int(query.get("max_blocks") or DEFAULT_MAX_BLOCKS)
            codes = [row["code"] for row in index[:cap]]
            names = {row["code"]: row["name"] for row in index}
        if not codes:
            raise DataSourceError(ErrorKind.BAD_DATA, "板块目录为空", provider=self.name, dataset=dataset)
        from datetime import date

        from app.engine.trading_calendar import latest_allowed_quote_date

        trade_date = latest_allowed_quote_date(date.today())
        out: dict[str, dict[str, Any]] = {}
        for block_code in codes:
            try:
                rows = ths_client.call(
                    "market_data_block", block_code, "基础数据", dataset=dataset, timeout=query.get("timeout")
                )
            except DataSourceError as exc:
                # 单板块失败不该拖垮整批：记一次指标后继续；全批都失败时由网关判空/熔断。
                if exc.kind in (ErrorKind.DISABLED, ErrorKind.TIMEOUT):
                    raise
                continue
            if rows:
                out[block_code] = _row(block_code, rows[0], names.get(block_code, ""), trade_date)
        if not out:
            raise DataSourceError(ErrorKind.BAD_DATA, "所有板块均无返回", provider=self.name, dataset=dataset)
        return out


_GATEWAY: DatasetGateway | None = None


def gateway() -> DatasetGateway:
    global _GATEWAY
    if _GATEWAY is None:
        _GATEWAY = DatasetGateway([ThsBlockBreadthProvider()])
    return _GATEWAY


def fetch_breadth(block_codes: list[str] | None = None, *, kind: str = "industry",
                  max_blocks: int | None = None) -> dict[str, dict[str, Any]]:
    """取板块广度：给定板块代码只查这些；不传则取行业板块前 ``max_blocks`` 个。"""
    query: dict[str, Any] = {"kind": kind}
    if block_codes:
        query["block_codes"] = list(block_codes)
    if max_blocks:
        query["max_blocks"] = int(max_blocks)
    return gateway().fetch_first(DATASET, query).rows


def blocks(kind: str = "all") -> list[dict[str, str]]:
    """板块目录（行业 / 概念 / 全部），供特征侧按行业名反查板块代码。"""
    return _load_index(kind)
