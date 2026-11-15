"""数据源适配模板：把 ``fetch -> validate -> map`` 的重复样板收敛到一处。

背景（2026-11-15，架构优化项①）
-------------------------------
接入一个新上游，原先要复制一整套样板：``_f()`` 浮点容错、``_ts()`` 代码转换、
``supports()`` 的 dataset 判断、``__init__`` 里的限流器、``fetch()`` 里手工拼
13 个字段的 dict。7 条协议 × 十几个 provider 累积下来，同一段归一化逻辑在仓库里
存在 8 份（``quote_realtime`` / ``web_backups`` / ``sina_realtime`` / ``tdx_quote`` /
``akshare_quote`` / ``qvix`` / ``eastmoney`` / ``akshare_backup``），改一处漏七处。

本模块提供三层能力，按侵入性从低到高：

1. **工具函数**：:func:`to_float` / :func:`code6` / :func:`to_ts_code` / :func:`pick`
   —— 已有的 ``_f()`` / ``_ts()`` 保留原名做别名转调，行为逐字节不变。
2. **行构造**：:func:`quote_row` —— 行情行的唯一构造入口，字段顺序与
   ``SCHEMAS["quote.realtime"]`` 对齐，缺字段自动补默认值。
3. **声明式 Provider**：:class:`ProviderBase` 与 :func:`make_provider` —— 新源只写
   "怎么把上游返回的行映射成标准行"，dataset 判断、限流、禁用开关、熔断全部由网关与
   基类接管。

用法（新增一个实时行情源的最小成本，约 15 行）::

    from app.services.datasource.adapter import ProviderBase, quote_row

    class MyQuoteProvider(ProviderBase):
        name = "my_quote"
        dataset = "quote.realtime"
        config_keys = (("myvendor", "quote"),)
        limiter_rpm = 120

        def _fetch(self, dataset, query):
            raw = my_client.batch(query["keys"])
            return {
                r["code"]: quote_row(code=r["code"], source=self.name, trade_date=r["date"],
                                     price=r["last"], open=r["open"], pre_close=r["prev"],
                                     high=r["high"], low=r["low"], volume=r["vol"], amount=r["amt"])
                for r in raw
            }

之后把它加进对应 ``gateway()`` 的 provider 列表即可；``supports`` / ``fetch`` 不用再写。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Iterator, Mapping, Sequence

logger = logging.getLogger(__name__)

# quote.realtime 的标准字段顺序（与 SCHEMAS 对齐，改动需同步 validate_rows）
QUOTE_FIELDS: tuple[str, ...] = (
    "code", "ts_code", "name", "trade_date", "quote_time", "price",
    "open", "pre_close", "high", "low", "volume", "amount", "source",
)

# 上游常见列名别名：一次映射，省掉每个 provider 手写 raw.get("昨收价") 之类的分支
_CODE_ALIASES = ("code", "代码", "symbol", "ts_code", "stock_code")
_NAME_ALIASES = ("name", "名称", "name_cn")
_PRICE_ALIASES = ("price", "价格", "close", "last", "last_price", "最新价")
_OPEN_ALIASES = ("open", "开盘价", "open_price")
_PRE_CLOSE_ALIASES = ("pre_close", "昨收价", "prev_close", "yclose", "settle")
_HIGH_ALIASES = ("high", "最高价", "high_price")
_LOW_ALIASES = ("low", "最低价", "low_price")
_VOLUME_ALIASES = ("volume", "成交量", "vol")
_AMOUNT_ALIASES = ("amount", "总金额", "成交额", "turnover")
_TIME_ALIASES = ("quote_time", "time", "trade_time", "tick_time")
_DATE_ALIASES = ("trade_date", "日期", "date")


def to_float(value: Any, default: float = 0.0) -> float:
    """容错转 float：``None`` / ``NaN`` / 非数字 / 空串统一落到 ``default``。

    与仓库里 8 份 ``_f()`` 的实现完全等价（含 ``v == v`` 的 NaN 判断），只是搬到了一处。
    """
    try:
        result = float(value) if value is not None and not (isinstance(value, float) and value != value) else default
    except (TypeError, ValueError):
        return default
    return result if result == result else default


def code6(value: Any) -> str:
    """``600000.SH`` / ``sh600000`` / ``600000`` -> ``600000``（只留 6 位数字主体）。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "." in raw:  # 600000.SH / 000001.SZ
        raw = raw.split(".")[0]
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else digits


def to_ts_code(value: Any) -> str:
    """6 位代码 -> ``600000.SH``（委托 tushare_client，保持既有口径一致）。"""
    from app.services.tushare_client import ts_code

    return ts_code(code6(value))


def pick(row: Mapping[str, Any], aliases: Sequence[str], default: Any = None) -> Any:
    """按别名顺序取第一个存在且非空的字段。"""
    for key in aliases:
        if key in row:
            value = row[key]
            if value is not None and value != "":
                return value
    return default


def quote_row(
    *,
    code: Any,
    source: str,
    trade_date: Any = "",
    ts_code: Any = "",
    name: Any = "",
    quote_time: Any = "",
    price: Any = 0.0,
    open: Any = 0.0,
    pre_close: Any = 0.0,
    high: Any = 0.0,
    low: Any = 0.0,
    volume: Any = 0.0,
    amount: Any = 0.0,
) -> dict[str, Any]:
    """构造一行标准行情。字段缺省一律补 ``0`` / 空串，调用方不必再写 ``_f(...)``。"""
    body = code6(code)
    return {
        "code": body,
        "ts_code": str(ts_code or "").strip() or (to_ts_code(body) if body else ""),
        "name": str(name or "").strip(),
        "trade_date": str(trade_date or ""),
        "quote_time": str(quote_time or ""),
        "price": to_float(price),
        "open": to_float(open),
        "pre_close": to_float(pre_close),
        "high": to_float(high),
        "low": to_float(low),
        "volume": to_float(volume),
        "amount": to_float(amount),
        "source": str(source or ""),
    }


def quote_row_from_raw(raw: Mapping[str, Any], *, source: str, **overrides: Any) -> dict[str, Any]:
    """从上游原始行 + 别名表一步生成标准行（中文列名的源用它就不必逐个写映射）。"""
    fields: dict[str, Any] = {
        "code": pick(raw, _CODE_ALIASES, ""),
        "name": pick(raw, _NAME_ALIASES, ""),
        "trade_date": pick(raw, _DATE_ALIASES, ""),
        "quote_time": pick(raw, _TIME_ALIASES, ""),
        "price": pick(raw, _PRICE_ALIASES, 0.0),
        "open": pick(raw, _OPEN_ALIASES, 0.0),
        "pre_close": pick(raw, _PRE_CLOSE_ALIASES, 0.0),
        "high": pick(raw, _HIGH_ALIASES, 0.0),
        "low": pick(raw, _LOW_ALIASES, 0.0),
        "volume": pick(raw, _VOLUME_ALIASES, 0.0),
        "amount": pick(raw, _AMOUNT_ALIASES, 0.0),
    }
    fields.update(overrides)
    return quote_row(source=source, **fields)


def normalize_mapping(rows: Any, *, source: str) -> dict[str, dict[str, Any]]:
    """把 provider 的任意返回（DataFrame / list[dict] / dict[code,row]）归一成 ``{code6: row}``。

    只做结构性转换，不做业务校验（空行过滤交给网关的 ``validate_rows``）。
    """
    if rows is None:
        return {}
    if isinstance(rows, dict):
        items: Iterator[tuple[Any, Any]] = iter(rows.items())
    elif hasattr(rows, "to_dict"):
        items = iter((r.get("code") or r.get("代码") or r.get("ts_code"), r) for r in rows.to_dict("records"))
    else:
        items = iter(((r.get("code") or r.get("代码") or r.get("ts_code"), r) for r in rows))
    out: dict[str, dict[str, Any]] = {}
    for key, value in items:
        body = code6(key if key is not None else (value or {}).get("code"))
        if not body:
            continue
        out[body] = dict(value) if isinstance(value, Mapping) else quote_row(code=body, source=source, price=value)
    return out


class ProviderBase:
    """声明式 provider 基类：子类只需给 ``name`` / ``dataset`` 并实现 :meth:`_fetch`。

    基类接管三件样板：

    * ``supports()``：dataset 相等 + 可选的 :meth:`accept`（query 级附加条件）
    * ``fetch()``：先取限流器（``limiter_rpm``），再调 :meth:`_fetch`
    * 惰性限流器：首次调用才建，避免 gateway() 组装时就拉起连接

    不接管的部分（保持既有约定）：禁用开关、熔断、超时、重试、指标全在 ``gateway.py``，
    provider 只管"取数 + 映射"。
    """

    name: str = ""
    dataset: str = ""
    config_keys: tuple[tuple[str, str], ...] = ()
    limiter_rpm: int | None = None
    upstream_breaker_keys: tuple[str, ...] = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not cls.name or not cls.dataset:
            if not getattr(cls, "_abstract", False):
                logger.warning("adapter provider %s 缺少 name/dataset 声明", cls.__name__)

    def __init__(self) -> None:
        self._lim = None

    # -- 限流 ---------------------------------------------------------------
    def limiter(self):
        if self.limiter_rpm is None:
            return None
        if self._lim is None:
            from app.services.tushare_transport import _make_limiter

            self._lim = _make_limiter(f"gateway:{self.name}", int(self.limiter_rpm))
        return self._lim

    # -- 网关契约 -----------------------------------------------------------
    def supports(self, dataset: str, query: Mapping[str, Any]) -> bool:
        return dataset == self.dataset and self.accept(query)

    def accept(self, query: Mapping[str, Any]) -> bool:  # noqa: ARG002 - 默认接受全部 query
        return True

    def fetch(self, dataset: str, query: Mapping[str, Any]) -> Any:
        lim = self.limiter()
        if lim is not None:
            lim.acquire()
        return self._fetch(dataset, query)

    def _fetch(self, dataset: str, query: Mapping[str, Any]) -> Any:  # pragma: no cover - 子类实现
        raise NotImplementedError(f"{type(self).__name__} 必须实现 _fetch()")

    def __repr__(self) -> str:  # 便于日志/拓扑页显示
        return f"<{type(self).__name__} name={self.name} dataset={self.dataset}>"


def make_provider(
    *,
    name: str,
    dataset: str,
    fetch_raw: Callable[[Mapping[str, Any]], Any],
    config_keys: tuple[tuple[str, str], ...] = (),
    accept: Callable[[Mapping[str, Any]], bool] | None = None,
    limiter_rpm: int | None = None,
    upstream_breaker_keys: tuple[str, ...] = (),
    doc: str = "",
) -> type[ProviderBase]:
    """一行注册一个新 provider（不想写类的话用它）。

    ``fetch_raw`` 只接收 ``query``，返回上游原始结构；异常照常抛给网关分类。
    """

    def _supports(self, ds: str, query: Mapping[str, Any]) -> bool:
        return ds == dataset and (accept(query) if accept else True)

    def _fetch(self, ds: str, query: Mapping[str, Any]) -> Any:  # noqa: ARG001
        return fetch_raw(query)

    return type(
        f"DynamicProvider_{name}",
        (ProviderBase,),
        {
            "name": name,
            "dataset": dataset,
            "config_keys": config_keys,
            "limiter_rpm": limiter_rpm,
            "upstream_breaker_keys": upstream_breaker_keys,
            "supports": _supports,
            "_fetch": _fetch,
            "__doc__": doc or f"{name}（make_provider 动态注册）",
            "_abstract": True,  # 跳过 __init_subclass__ 的告警（字段已齐）
        },
    )


def build_chain(providers: Sequence[Any]) -> list[Any]:
    """过滤 ``None`` 与已退役源，保持声明顺序 —— 各 ``gateway()`` 的统一收尾。"""
    from app.services.datasource import retired as _retired

    return [
        p for p in providers
        if p is not None and not _retired.provider_retired(getattr(p, "name", ""))
    ]
