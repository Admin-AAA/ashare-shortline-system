"""过滤审计快照的压缩编解码。

一个 run 的审计快照是全市场每只票一份诊断对象，原始 JSON 平均 3.3MB、最大 9.6MB。
全表 1133 行合计 3.71GB，占整个数据库 7%，而内容是同构的重复键名与中文短句，
zlib 压缩后只剩 6.4%（0.24GB）。压缩放在应用层而不是 MySQL 的 COMPRESS()，
因为后者对空串返回零字节、对超长输入受 max_allowed_packet 约束，行为不够可预期。

存的是 UTF-8 JSON 字节流压缩后的裸 zlib 流（无长度前缀），空审计存 b""。
"""
from __future__ import annotations

import json
import zlib
from typing import Any

# zlib 压缩级别 6：3.71GB → 0.24GB，单行 9.6MB 压缩约 31ms、解压约 5ms。
_COMPRESS_LEVEL = 6


def encode_audit_payload(audit: Any) -> bytes:
    """把审计字典序列化并压缩成待落库的字节串；空审计存空字节串。"""
    if not audit:
        return b""
    return zlib.compress(json.dumps(audit, ensure_ascii=False).encode("utf-8"), _COMPRESS_LEVEL)


def decode_audit_payload(blob: bytes | None) -> dict[str, Any]:
    """把落库的压缩字节串还原成审计字典；空值返回空字典。"""
    if not blob:
        return {}
    return json.loads(zlib.decompress(blob).decode("utf-8"))
