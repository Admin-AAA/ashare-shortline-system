"""Decoder for the tagged canonical bytes defined by snapshot contracts."""
from __future__ import annotations

import base64
from datetime import date, datetime, time
from decimal import Decimal
import json
from typing import Any

from app.services.auxiliary.snapshot_contracts import (
    CanonicalPayload,
    SnapshotContractError,
    canonical_payload_bytes,
)


def _decode_node(node: Any) -> Any:
    if not isinstance(node, list) or not node or not isinstance(node[0], str):
        raise SnapshotContractError("invalid canonical payload node")
    tag = node[0]
    if tag == "null" and len(node) == 1:
        return None
    if len(node) != 2:
        raise SnapshotContractError(f"invalid canonical payload node for tag {tag!r}")
    value = node[1]
    if tag == "boolean" and isinstance(value, bool):
        return value
    if tag == "string" and isinstance(value, str):
        return value
    if tag == "integer" and isinstance(value, str):
        return int(value)
    if tag in {"decimal", "decimal_special"} and isinstance(value, str):
        return Decimal(value)
    if tag == "float_special" and value in {"nan", "infinity", "-infinity"}:
        return {"nan": float("nan"), "infinity": float("inf"), "-infinity": float("-inf")}[value]
    if tag == "datetime" and isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    if tag == "date" and isinstance(value, str):
        return date.fromisoformat(value)
    if tag == "time" and isinstance(value, str):
        return time.fromisoformat(value)
    if tag == "bytes" and isinstance(value, str):
        return base64.b64decode(value.encode("ascii"), validate=True)
    if tag == "tuple" and isinstance(value, list):
        return tuple(_decode_node(item) for item in value)
    if tag == "list" and isinstance(value, list):
        return [_decode_node(item) for item in value]
    if tag == "map" and isinstance(value, list):
        result: dict[str, Any] = {}
        for item in value:
            if not isinstance(item, list) or len(item) != 2 or not isinstance(item[0], str):
                raise SnapshotContractError("invalid canonical map entry")
            key = item[0]
            if key in result:
                raise SnapshotContractError(f"duplicate canonical map key: {key!r}")
            result[key] = _decode_node(item[1])
        return result
    raise SnapshotContractError(f"unsupported or malformed canonical payload tag: {tag!r}")


def decode_canonical_payload(payload: CanonicalPayload) -> Any:
    """Decode and revalidate one canonical payload before projection use."""
    try:
        node = json.loads(payload.content.decode("utf-8"))
        value = _decode_node(node)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise SnapshotContractError("invalid canonical payload bytes") from exc
    if canonical_payload_bytes(value) != payload.content:
        raise SnapshotContractError("canonical payload does not round-trip exactly")
    return value


__all__ = ["decode_canonical_payload"]
