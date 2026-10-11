"""时间工具。

数据库中的历史字段约定使用无时区 UTC ``datetime``；统一从带时区的时钟
生成再去掉 ``tzinfo``，避免 Python 3.12 弃用 ``datetime.utcnow()``。
"""

from datetime import datetime, timezone


def utcnow_naive() -> datetime:
    """返回无时区 UTC 当前时间，供既有 SQL ``DATETIME`` 字段使用。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_isoformat(value: datetime | None) -> str | None:
    """把库里的无时区 UTC 时间序列化为带 ``Z`` 的 ISO 字符串。

    不带时区的 ISO 串会被浏览器 ``new Date()`` 当本地时间解析，北京时间页面上
    就早 8 小时；带上 ``Z`` 后前端按 UTC 解析再显示为本地时间。
    """
    if value is None:
        return None
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return aware.isoformat().replace("+00:00", "Z")
