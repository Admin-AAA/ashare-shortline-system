"""Deterministic canonical JSON primitives shared by immutable engine contracts."""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
import hashlib
import json
import math
from typing import Any, Mapping


def canonicalize(value: Any) -> Any:
    """Convert supported values into deterministic JSON-compatible primitives."""

    if isinstance(value, Enum):
        return canonicalize(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: canonicalize(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON mapping keys must be strings")
            normalized[key] = canonicalize(item)
        return normalized
    if isinstance(value, (tuple, list)):
        return [canonicalize(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((canonicalize(item) for item in value), key=canonical_json)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise TypeError("canonical JSON datetime values must be timezone-aware")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON does not support non-finite floats")
        return 0.0 if value == 0 else value
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return canonical UTF-8 JSON with sorted keys and no insignificant whitespace."""

    return json.dumps(
        canonicalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_fingerprint(value: Any) -> str:
    """Return the lowercase SHA-256 digest of :func:`canonical_json`."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


# Domain-language alias retained for existing callers.
fingerprint = canonical_fingerprint


def content_hash(payload: bytes) -> str:
    """Return a SHA-256 digest for immutable binary artifact content."""

    if not isinstance(payload, bytes):
        raise TypeError("content hash payload must be bytes")
    return hashlib.sha256(payload).hexdigest()


class CanonicalValue:
    """Mixin for immutable dataclasses that have canonical JSON identity."""

    def canonical_payload(self) -> dict[str, Any]:
        payload = canonicalize(self)
        if not isinstance(payload, dict):
            raise TypeError("canonical domain payload must be an object")
        return payload

    def to_canonical_json(self) -> str:
        return canonical_json(self.canonical_payload())

    def fingerprint(self) -> str:
        return canonical_fingerprint(self.canonical_payload())


__all__ = [
    "CanonicalValue",
    "canonical_fingerprint",
    "canonical_json",
    "canonicalize",
    "content_hash",
    "fingerprint",
]
