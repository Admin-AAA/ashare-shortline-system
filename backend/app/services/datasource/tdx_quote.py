"""通达信行情（pytdx，公开 HQ TCP/7709）：``quote.realtime`` 的免费备份。

链路位置（``quote_realtime.py``）：promax rt_k → 新浪 → **通达信** → 腾讯（flag）→
AKShare 快照 → quantdata rt_min → 小石。

放在新浪之后、腾讯之前：公开 HQ 地址会轮换，连接拒绝很常见，单次探测的可靠性低于
新浪 HTTP。新浪已经补上的代码不会再到这里（fill）。新浪也缺的代码先走这条无密钥
TCP，避免打到 AKShare 全市场翻页和 quantdata / 小石额度。整段调用有硬超时
（``TDX_DEADLINE_SECONDS``），连不上就抛 ``DataSourceError``，熔断打开后网关直接跳过。

选型：用 pytdx 的 ``TdxHq_API.get_security_quotes``（单包最多 80 只）。mootdx 仍依赖
pytdx，默认还会做 best-IP 探测和心跳线程，和「快失败、把预算留给下一级」冲突。
不自写二进制协议。无 API key。

单位：pytdx 已把价格除以 100（元）；``vol`` 是协议里的整数成交量（A 股为股）；
``amount`` 经 ``get_volume`` 解码为元。与新浪 ``realtime_quote`` / AKShare 快照一致。
行情包没有证券名称，``name`` 留空；没有交易日，``trade_date`` 用交易日历的最近报价日。

开关：``ASHARE_TDX_QUOTE_ENABLED=false`` 整体关闭；可选
``ASHARE_TDX_QUOTE_HOSTS=ip:port,ip:port`` 覆盖默认公开主机（不是密钥）。
管理页 ``data_source_config(tdx, quote)`` 可运行时关闭。
"""
from __future__ import annotations

import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FutTimeout
from datetime import date
from typing import Any, Callable, Mapping

from app.services.datasource.gateway import DataSourceError, ErrorKind

DATASET = "quote.realtime"
SOURCE = "tdx_quote"
# pytdx GetSecurityQuotes 单包上限。再大服务器会直接丢掉或解析错位。
BATCH = 80
# 单次 fill 最多补这么多只，剩下交给下一级，避免对公开 HQ 连打几十包。
MAX_CODES = BATCH * 3
# 一次调用最多换这么多台主机。黑名单式的 SYN 超时不能把 3s 实时预算吃光。
MAX_HOST_TRIES = 2
CONNECT_TIMEOUT = 0.4
READ_TIMEOUT = 0.7
TDX_DEADLINE_SECONDS = 1.0
TDX_RPM = 60
# 市场号：0 深圳，1 上海，2 北京（北交所）。get_security_list 对北京不可用，行情可以。
MARKET_SZ, MARKET_SH, MARKET_BJ = 0, 1, 2
_SUFFIX = {MARKET_SZ: "SZ", MARKET_SH: "SH", MARKET_BJ: "BJ"}

# 公开通达信 HQ。取自 pytdx ``util/best_ip.py`` 的 stock_ip（标准行情，不是扩展/期货），
# 按城市散开。这些地址会失效，可用 ASHARE_TDX_QUOTE_HOSTS 覆盖。不是凭证。
DEFAULT_HOSTS: tuple[tuple[str, int], ...] = (
    ("106.120.74.86", 7709),    # 北京
    ("114.80.80.222", 7709),    # 上海
    ("113.105.73.88", 7709),    # 深圳
    ("218.108.98.244", 7709),   # 杭州
    ("119.147.171.206", 7709),  # 广州
    ("115.238.56.198", 7709),
    ("180.153.18.170", 7709),
    ("123.125.108.23", 7709),
)

_TIME = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})")
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tdx-quote")
_HOST_LOCK = threading.Lock()
_HOST_CURSOR = 0
_LIMITER: Any = None
_LIMITER_LOCK = threading.Lock()


def _f(v: Any) -> float:
    try:
        x = float(v)
        return x if x == x else 0.0
    except (TypeError, ValueError):
        return 0.0


def _setting(name: str, default: Any) -> Any:
    try:
        from app.core.config import get_settings

        return getattr(get_settings(), name, default)
    except Exception:  # noqa: BLE001
        return default


def enabled() -> bool:
    return bool(_setting("tdx_quote_enabled", True))


def _under_pytest() -> bool:
    try:
        from app.core import metrics as _m

        return _m._process_role() == "pytest"
    except Exception:  # noqa: BLE001
        return False


def parse_hosts(raw: str) -> list[tuple[str, int]]:
    """``ip:port,ip:port`` → hosts。空串表示用默认公开列表。端口缺省 7709。"""
    text = str(raw or "").strip()
    if not text:
        return list(DEFAULT_HOSTS)
    out: list[tuple[str, int]] = []
    for part in text.split(","):
        item = part.strip()
        if not item:
            continue
        host, _, port_s = item.rpartition(":")
        if not host:
            host, port_s = item, "7709"
        try:
            port = int(port_s)
        except ValueError as exc:
            raise DataSourceError(
                ErrorKind.CONFIGURATION, f"bad tdx host {item!r}", provider=SOURCE, dataset=DATASET
            ) from exc
        if not host or not (0 < port < 65536):
            raise DataSourceError(
                ErrorKind.CONFIGURATION, f"bad tdx host {item!r}", provider=SOURCE, dataset=DATASET
            )
        out.append((host, port))
    if not out:
        raise DataSourceError(ErrorKind.CONFIGURATION, "tdx host list empty", provider=SOURCE, dataset=DATASET)
    return out


def configured_hosts() -> list[tuple[str, int]]:
    return parse_hosts(str(_setting("tdx_quote_hosts", "") or ""))


def _rotated_hosts() -> list[tuple[str, int]]:
    """每次调用换一台开头，避免死主机一直排在第一位。单次只试 ``MAX_HOST_TRIES`` 台。"""
    global _HOST_CURSOR
    hosts = configured_hosts()
    with _HOST_LOCK:
        i = _HOST_CURSOR % len(hosts)
        _HOST_CURSOR += 1
    ordered = hosts[i:] + hosts[:i]
    return ordered[:MAX_HOST_TRIES]


def code6_and_market(key: str) -> tuple[str, int] | None:
    """请求键 → (6 位代码, 通达信市场号)。

    带后缀时认后缀（``920001.BJ`` 不会被当成上海）。否则：
    北交所 ``4/8`` 以及 ``92`` 开头为北京，``5/6/9`` 为上海，其余为深圳。
    """
    raw = str(key or "").strip().upper()
    if not raw:
        return None
    code, _, sfx = raw.partition(".")
    if len(code) != 6 or not code.isdigit():
        return None
    if sfx in _SUFFIX.values():
        market = {v: k for k, v in _SUFFIX.items()}[sfx]
        return code, market
    if sfx:
        return None
    if code.startswith(("92", "4", "8")):
        return code, MARKET_BJ
    if code.startswith(("5", "6", "9")):
        return code, MARKET_SH
    return code, MARKET_SZ


def quote_time_of(servertime: Any) -> str:
    """pytdx ``servertime``（如 ``15:00:03.600`` / ``9:25:00``）→ ``HH:MM:SS``。解析不了就留空。"""
    m = _TIME.match(str(servertime or "").strip())
    if not m:
        return ""
    h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if h > 23 or mi > 59 or s > 59:
        return ""
    return f"{h:02d}:{mi:02d}:{s:02d}"


def normalize_quotes(
    raw: list[Mapping[str, Any]] | None,
    *,
    quote_date: str,
    requested: Mapping[str, tuple[int, list[str]]],
) -> dict[str, dict[str, Any]]:
    """pytdx ``get_security_quotes`` 行 → schema 行。``price<=0`` 的不占坑，留给下一级。

    ``requested`` 是 code → (请求时的市场号, 原始键)。后缀以请求为准，不跟服务器回显走。
    """
    out: dict[str, dict[str, Any]] = {}
    for row in raw or []:
        code = str(row.get("code") or "").strip()
        spec = requested.get(code)
        if spec is None:
            continue
        price = _f(row.get("price"))
        if price <= 0:
            continue
        market, keys = spec
        suffix = _SUFFIX.get(market, "SZ")
        item = {
            "code": code,
            "ts_code": f"{code}.{suffix}",
            "name": str(row.get("name") or "").strip(),
            "trade_date": quote_date,
            "quote_time": quote_time_of(row.get("servertime")),
            "price": price,
            "open": _f(row.get("open")),
            "pre_close": _f(row.get("last_close")),
            "high": _f(row.get("high")),
            "low": _f(row.get("low")),
            "volume": _f(row.get("vol")),
            "amount": _f(row.get("amount")),
            "source": SOURCE,
        }
        for key in keys:
            out[key] = item
    return out


def _kind(exc: BaseException) -> ErrorKind:
    if isinstance(exc, DataSourceError):
        return exc.kind
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if isinstance(exc, (TimeoutError, socket.timeout)) or "timeout" in name or "timeout" in msg:
        return ErrorKind.TIMEOUT
    return ErrorKind.UPSTREAM


def _limiter():
    global _LIMITER
    if _LIMITER is None:
        with _LIMITER_LOCK:
            if _LIMITER is None:
                from app.services.tushare_transport import _make_limiter

                _LIMITER = _make_limiter("gateway:tdx_quote", TDX_RPM)
    return _LIMITER


def _acquire() -> None:
    """占一个限速名额。窗口已满就立刻让路，不在公开 HQ 的限速上睡觉。"""
    lim = _limiter()
    headroom = getattr(lim, "headroom", None)
    if headroom is not None and headroom() <= 0:
        raise DataSourceError(ErrorKind.DATA_PENDING, "tdx quote rate-limited", provider=SOURCE, dataset=DATASET)
    lim.acquire()


def _safe_disconnect(api: Any) -> None:
    try:
        api.disconnect()
    except Exception:  # noqa: BLE001 - 断开失败不影响降级
        pass


def _default_client() -> Any:
    if _under_pytest():
        # 测试进程绝不拨公开 HQ。需要行为的测试注入 client_factory。
        raise DataSourceError(ErrorKind.DISABLED, "tdx quote disabled under pytest", provider=SOURCE, dataset=DATASET)
    from pytdx.hq import TdxHq_API

    # heartbeat/auto_retry 都会在超时之后继续占着调用，和快失败相反。
    return TdxHq_API(multithread=False, heartbeat=False, auto_retry=False, raise_exception=True)


def _connect(api: Any, hosts: list[tuple[str, int]], deadline: float) -> None:
    last: BaseException | None = None
    for host, port in hosts:
        if time.monotonic() >= deadline:
            break
        try:
            ok = api.connect(host, port, time_out=CONNECT_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - 换下一台
            last = exc
            _safe_disconnect(api)
            continue
        if not ok:
            last = TimeoutError(f"tdx connect failed {host}:{port}")
            _safe_disconnect(api)
            continue
        sock = getattr(api, "client", None)
        if sock is not None and hasattr(sock, "settimeout"):
            sock.settimeout(READ_TIMEOUT)
        return
    if last is None:
        last = TimeoutError("tdx connect deadline exceeded")
    raise DataSourceError(_kind(last), f"tdx connect failed: {last}", provider=SOURCE, dataset=DATASET) from last


def _fetch_blocking(keys: list[str], client_factory: Callable[[], Any]) -> dict[str, dict[str, Any]]:
    from app.engine.trading_calendar import latest_allowed_quote_date

    parsed: list[tuple[str, str, int]] = []
    requested: dict[str, tuple[int, list[str]]] = {}
    for key in keys:
        item = code6_and_market(str(key))
        if item is None:
            continue
        code, market = item
        slot = requested.get(code)
        if slot is None:
            parsed.append((str(key), code, market))
            requested[code] = (market, [str(key)])
        else:
            slot[1].append(str(key))
    if not parsed:
        return {}
    parsed = parsed[:MAX_CODES]
    quote_date = latest_allowed_quote_date(date.today())
    deadline = time.monotonic() + TDX_DEADLINE_SECONDS
    last: DataSourceError | None = None
    # 一台一台试。连上但第一包是空响应（僵尸节点）时换下一台。
    # 已经拿到价格的部分直接返回，缺的代码交给链路下一级。
    for host, port in _rotated_hosts():
        if time.monotonic() >= deadline:
            break
        api = client_factory()
        out: dict[str, dict[str, Any]] = {}
        try:
            try:
                _connect(api, [(host, port)], deadline)
            except DataSourceError as exc:
                last = exc
                continue
            abandon = False
            for i in range(0, len(parsed), BATCH):
                if time.monotonic() >= deadline:
                    break
                chunk = parsed[i:i + BATCH]
                try:
                    _acquire()
                    rows = api.get_security_quotes([(market, code) for _key, code, market in chunk])
                except Exception as exc:  # noqa: BLE001
                    if out:
                        return out
                    last = exc if isinstance(exc, DataSourceError) else DataSourceError(
                        _kind(exc), str(exc), provider=SOURCE, dataset=DATASET
                    )
                    abandon = True
                    break
                got = normalize_quotes(rows, quote_date=quote_date, requested=requested)
                out.update(got)
                if not rows and not out:
                    # 第一包就是空响应（连上了但不给行情）：换下一台。
                    # 有行但价格都 <=0 不算僵尸，停牌不占坑，继续本台后面的包。
                    abandon = True
                    break
            if out:
                return out
            if not abandon:
                # 服务器有响应，只是没有可交易价格（停牌）。换主机不会更好。
                return {}
        finally:
            _safe_disconnect(api)
    if last is not None:
        raise last
    return {}


class TdxQuoteProvider:
    """公开通达信 HQ 的批量快照。只补 fill 模式里仍然缺的代码。"""

    name = SOURCE
    config_keys = (("tdx", "quote"),)

    def __init__(self, client_factory: Callable[[], Any] | None = None) -> None:
        self._client_factory = client_factory or _default_client

    def supports(self, dataset: str, query: Mapping[str, Any]) -> bool:
        return dataset == DATASET and enabled()

    def fetch(self, dataset: str, query: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        from app.services.datasource.cache import submit_ctx

        keys = [str(k) for k in query.get("keys") or []]
        fut = submit_ctx(_POOL, _fetch_blocking, keys, self._client_factory)
        try:
            return fut.result(timeout=TDX_DEADLINE_SECONDS)
        except _FutTimeout:
            fut.cancel()
            raise DataSourceError(
                ErrorKind.TIMEOUT,
                f"tdx quote exceeded {TDX_DEADLINE_SECONDS}s",
                provider=self.name,
                dataset=dataset,
            ) from None
        except DataSourceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DataSourceError(_kind(exc), str(exc), provider=self.name, dataset=dataset) from exc
