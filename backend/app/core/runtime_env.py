"""运行时环境变量读取：``os.environ`` 优先，其次 ``backend/.env``。

**为什么需要这个模块**：pydantic ``Settings`` 会读 ``.env``，但**不会**把键导出到
``os.environ``。于是所有在函数/模块里直接用 ``os.environ.get(...)`` 读取的开关，即使
写进了 ``.env`` 也读不到——``TUSHARE_SHARED_LIMITER_DB`` 就是这样长期"配了但不生效"
（跨进程限频实际一直是关闭的，backend 与 Prefect worker 各持一份配额）。

本模块统一兜底，并支持 ``ASHARE_`` 前缀别名，让两种写法都生效：
    TUSHARE_SHARED_LIMITER_DB=...            # 历史写法（shared_rate_limiter.ENV_VAR）
    ASHARE_TUSHARE_SHARED_LIMITER_DB=...     # 与 Settings 前缀一致

只读、无缓存副作用（文件小，解析成本可忽略）；任何异常返回空串，绝不抛出。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


@lru_cache(maxsize=1)
def _dotenv() -> dict[str, str]:
    """解析 backend/.env 成字典（行内 ``#`` 注释会被剥掉，引号会被去掉）。"""
    out: dict[str, str] = {}
    try:
        text = ENV_FILE.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[name.strip()] = value.strip()
    return out


def dotenv_value(*names: str, default: str = "") -> str:
    """**只**读 ``backend/.env`` 文件，忽略进程环境变量。

    用于区分"测试显式 setenv 的值"与"被 ``load_dotenv()`` 注入 os.environ 的 .env 值"：
    ``scripts/`` 下的回测脚本带 ``load_dotenv()``，被测试 import 后会把 .env 全量灌进
    ``os.environ``，此时单看 os.environ 无法判断来源。
    """
    for name in names:
        if not name:
            continue
        raw = (_dotenv().get(name) or "").strip()
        if raw:
            return raw
    return default


def env_value(*names: str, default: str = "") -> str:
    """按顺序取第一个非空值：进程环境变量 → ``.env`` 文件；全都没有则返回 ``default``。"""
    for name in names:
        if not name:
            continue
        raw = (os.environ.get(name) or "").strip()
        if raw:
            return raw
        raw = (_dotenv().get(name) or "").strip()
        if raw:
            return raw
    return default


def env_flag(*names: str, default: bool = False) -> bool:
    raw = env_value(*names).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on", "y")


def reset_cache() -> None:
    """测试用：改了 .env 之后丢弃解析缓存。"""
    _dotenv.cache_clear()
