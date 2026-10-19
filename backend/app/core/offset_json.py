"""全局 JSON 响应：把无时区的 ISO 日期时间串补上时区偏移。

约定（app/core/time.py）：库里/服务端的无时区 datetime 一律是 UTC。FastAPI 序列化后会变成
``2026-10-19T01:23:45`` 这样的无偏移串，浏览器会当本地时间解析。这里只改写严格匹配
``YYYY-MM-DDTHH:MM:SS[.ffffff]`` 的字符串值（必须带 T 和秒），补 ``Z``；已带偏移/Z、纯日期、
空格分隔等其它格式一律不动。北京时间的无时区字段已在源头改为带 +08:00 输出。
"""
from __future__ import annotations

import re
from typing import Any

from fastapi.responses import JSONResponse

_NAIVE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?$")


def add_utc_offset(obj: Any) -> Any:
    if isinstance(obj, str):
        return obj + "Z" if _NAIVE_ISO.match(obj) else obj
    if isinstance(obj, dict):
        return {k: add_utc_offset(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [add_utc_offset(v) for v in obj]
    return obj


class OffsetJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return super().render(add_utc_offset(content))
