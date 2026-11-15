"""同花顺 thsdk 客户端：三条 P0 接入点（板块广度 / 实时行情冗余 / 个股快讯）共用。

分工：本模块只负责「连得上、调得稳、错得清」，字段归一化留给各自的 provider：

* ``quote.realtime``      → ``quote_realtime.ThsRealtimeProvider``（rt_k 之后的行情备源）
* ``aux.sector_breadth``  → ``sector_breadth.ThsBlockBreadthProvider``（板块广度）
* ``aux.news``            → ``market_auxiliary._fetch_ths_news``（按代码取个股快讯）

实测约束（2026-11-15，thsdk 1.7.18）：

* 进程内只维持一条会话；``market_data_cn`` 一次只能查**同一市场**的代码，混沪深直接报
  「一次性查询多支股票必须市场代码相同」，调用方必须按 USHA/USZA 分组。
* 官方限频约 20ms/次，超了报「兄弟，太快啦」；这里用一把全局锁串行 + 最小间隔节流。
* 上游无效值哨兵是 ``4294967295``（大单金额、内外盘、tick 成交方向），按缺失处理，
  避免哨兵被当成真实数值写进特征。
* 无账号时自动使用临时游客账户，官方明示「可能随时失效，不适合生产环境」；
  生产请配 ``ASHARE_THS_USERNAME`` / ``ASHARE_THS_PASSWORD`` / ``ASHARE_THS_MAC``。
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FutTimeout
from typing import Any

from app.services.datasource.gateway import DataSourceError, ErrorKind

logger = logging.getLogger(__name__)

PROVIDER = "ths"
SENTINEL = 4294967295  # 上游「无此字段」哨兵，按缺失处理
# 单次调用在独立线程里跑，超时就抛 TIMEOUT 交给网关熔断；底层 socket 由 thsdk 自己收尾。
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="thsdk")
_CLIENT_LOCK = threading.Lock()   # 连接生命周期
_CALL_LOCK = threading.Lock()     # 串行调用 + 节流（thsdk 会话非线程安全）
_CLIENT: Any = None
_LAST_CALL = 0.0


# ── 配置读取（延迟 import，避免 config ←→ 数据源模块的加载环） ──────────────
def _settings() -> Any:
    from app.core.config import get_settings

    return get_settings()


def is_enabled() -> bool:
    """ASHARE_THS_ENABLED=false 时整体关闭，三条接入点同时失效（数据源管理页仍可见开关）。"""
    try:
        return bool(getattr(_settings(), "ths_enabled", True))
    except Exception:  # noqa: BLE001 - 配置读不到时按启用，让网关开关兜底
        return True


def venue_for(code: str) -> str:
    """6 位代码 → thsdk 市场前缀。ST/退市等首字母异常代码按空处理，由调用方跳过。"""
    digits = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(digits) != 6:
        return ""
    if digits[0] == "6":
        return "USHA"
    if digits[0] in ("0", "3"):
        return "USZA"
    if digits[0] in ("4", "8", "9"):
        return "USTM"
    return ""


def clean_number(value: Any) -> float | None:
    """哨兵 / 空值 / 非数值统一成 None；其余转 float。

    哨兵必须**精确等值**判断：``4294967295`` 只表示「无效」，但真实金额/市值经常远超 4.29e9
    （实测半导体板块总金额 2.3e11、流通市值 6.4e12），用 ``>=`` 会把正常大数误杀成 None。
    """
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out == SENTINEL or out == -SENTINEL:  # NaN 或哨兵（精确匹配）
        return None
    return out


def clean_int(value: Any) -> int | None:
    num = clean_number(value)
    return None if num is None else int(num)


def _client() -> Any:
    """惰性建连：配了账号就用账号，否则 THS({}) 走游客账户。"""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is not None:
            return _CLIENT
        try:
            from thsdk import THS
        except ImportError as exc:  # pragma: no cover - 依赖缺失按配置错误处理
            raise DataSourceError(
                ErrorKind.CONFIGURATION, "thsdk 未安装（pip install thsdk）", provider=PROVIDER, dataset="ths"
            ) from exc
        st = _settings()
        ops: dict[str, Any] = {}
        username = str(getattr(st, "ths_username", "") or "").strip()
        password = str(getattr(st, "ths_password", "") or "").strip()
        mac = str(getattr(st, "ths_mac", "") or "").strip()
        if username and password:
            ops = {"username": username, "password": password}
            if mac:
                ops["mac"] = mac
        else:
            logger.info("thsdk 未配置账号，使用临时游客账户（官方提示：可能随时失效）")
        client = THS(ops or None)
        resp = client.connect()
        if getattr(resp, "error", ""):
            raise DataSourceError(
                ErrorKind.UPSTREAM, f"thsdk 连接失败: {resp.error}", provider=PROVIDER, dataset="ths"
            )
        _CLIENT = client
    return _CLIENT


def close() -> None:
    """断开会话（测试/进程退出用）。"""
    global _CLIENT
    with _CLIENT_LOCK:
        client, _CLIENT = _CLIENT, None
    if client is not None:
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001 - 已断开就忽略
            pass


_THS_LIMITER: list = []


def _ths_limiter():
    """可选的分钟级限流器（``ASHARE_THS_RATE_LIMIT_PER_MINUTE`` > 0 时才建）。"""
    if _THS_LIMITER:
        return _THS_LIMITER[0]
    lim = None
    try:
        rpm = int(getattr(_settings(), "ths_rate_limit_per_minute", 0) or 0)
    except Exception:  # noqa: BLE001
        rpm = 0
    if rpm > 0:
        from app.services.tushare_transport import _make_limiter

        lim = _make_limiter("gateway:ths", rpm)
    _THS_LIMITER.append(lim)
    return lim


def reset_limiter_cache() -> None:
    """测试用：配置变化后重建限流器。"""
    _THS_LIMITER.clear()


def _throttle() -> None:
    """全局最小调用间隔，防「兄弟，太快啦」。

    两层：① 配置的 ``ths_min_interval_seconds``（默认 0.06s，官方 20ms 的 3 倍余量）；
    ② 若配了 ``ths_rate_limit_per_minute``，再叠加分钟窗口限流（跨进程，见 shared_rate_limiter）。
    """
    lim = _ths_limiter()
    if lim is not None:
        lim.acquire()
    global _LAST_CALL
    try:
        gap = float(getattr(_settings(), "ths_min_interval_seconds", 0.06) or 0.0)
    except Exception:  # noqa: BLE001
        gap = 0.06
    wait = gap - (time.monotonic() - _LAST_CALL)
    if wait > 0:
        time.sleep(wait)
    _LAST_CALL = time.monotonic()


def _map_exception(exc: Exception, api: str, dataset: str) -> DataSourceError:
    text = str(exc)
    if "太快" in text or "频率" in text or "频繁" in text:
        kind = ErrorKind.RATE_LIMIT
    elif "超时" in text or "timeout" in text.lower():
        kind = ErrorKind.TIMEOUT
    elif "参数" in text or "必须" in text or "不支持" in text:
        kind = ErrorKind.INVALID_REQUEST
    else:
        kind = ErrorKind.NETWORK
    return DataSourceError(kind, f"thsdk {api} 失败: {text[:200]}", provider=PROVIDER, dataset=dataset)


def _rows(resp: Any, api: str, dataset: str) -> list[dict[str, Any]]:
    """Response → list[dict]。data 为空但 df 有内容时退回 df。"""
    if resp is None:
        raise DataSourceError(ErrorKind.UPSTREAM, f"thsdk {api} 返回空响应", provider=PROVIDER, dataset=dataset)
    if getattr(resp, "success", True) is False or getattr(resp, "error", ""):
        raise DataSourceError(
            ErrorKind.UPSTREAM, f"thsdk {api}: {str(getattr(resp, 'error', '') or 'success=False')[:200]}",
            provider=PROVIDER, dataset=dataset,
        )
    data = getattr(resp, "data", None)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    df = getattr(resp, "df", None)
    if df is not None and hasattr(df, "to_dict"):
        try:
            return [row for row in df.to_dict("records") if isinstance(row, dict)]
        except Exception:  # noqa: BLE001 - df 结构异常按空处理
            return []
    return []


def call(api: str, *args: Any, dataset: str = "ths", timeout: float | None = None, **kwargs: Any) -> list[dict[str, Any]]:
    """调一个 thsdk 方法，返回 list[dict]；错误统一映射为 ``DataSourceError``。"""
    if not is_enabled():
        raise DataSourceError(ErrorKind.DISABLED, "ASHARE_THS_ENABLED=false", provider=PROVIDER, dataset=dataset)

    def _run() -> list[dict[str, Any]]:
        with _CALL_LOCK:
            client = _client()
            _throttle()
            try:
                resp = getattr(client, api)(*args, **kwargs)
            except DataSourceError:
                raise
            except Exception as exc:  # noqa: BLE001 - 上游异常按网关口径归类
                raise _map_exception(exc, api, dataset) from exc
            return _rows(resp, api, dataset)

    limit = timeout
    if limit is None:
        try:
            limit = float(getattr(_settings(), "ths_timeout_seconds", 5.0) or 5.0)
        except Exception:  # noqa: BLE001
            limit = 5.0
    fut = _POOL.submit(_run)
    try:
        return fut.result(timeout=limit)
    except _FutTimeout:
        raise DataSourceError(
            ErrorKind.TIMEOUT, f"thsdk {api} 超过 {limit}s", provider=PROVIDER, dataset=dataset
        ) from None


def probe() -> dict[str, Any]:
    """连通性自检：返回账户类型 + 各接入点的可用性（脚本/排障用，不进业务链）。"""
    out: dict[str, Any] = {"enabled": is_enabled(), "client": None, "checks": {}}
    client = _client()
    ops = getattr(client, "ops", None) or {}
    out["client"] = "guest" if str(ops.get("username", "")).startswith("thsguest_") else "account"
    for name, fn in (
        ("market_data_cn", lambda: call("market_data_cn", "USHA600519", dataset="quote.realtime")),
        ("market_data_block", lambda: call("market_data_block", "URFI881121", "基础数据", dataset="aux.sector_breadth")),
        ("news", lambda: call("news", dataset="aux.news")),
    ):
        try:
            out["checks"][name] = {"ok": True, "rows": len(fn())}
        except Exception as exc:  # noqa: BLE001 - 自检要把错误带出来而不是抛
            out["checks"][name] = {"ok": False, "error": str(exc)[:200]}
    return out
