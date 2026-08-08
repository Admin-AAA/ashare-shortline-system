"""时间工具。

数据库中的历史字段约定使用无时区 UTC ``datetime``；统一从带时区的时钟
生成再去掉 ``tzinfo``，避免 Python 3.12 弃用 ``datetime.utcnow()``。
"""

from datetime import datetime, timezone


def utcnow_naive() -> datetime:
    """返回无时区 UTC 当前时间，供既有 SQL ``DATETIME`` 字段使用。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)
