"""Scope_Guard: fail-closed deferred-initiative and brokerage-access denial.

Production-evidence-risk-hardening spec S0 task 0.10 (Requirements 12.1-12.7):

- D1 full historical backfill, D2 new alpha, and D3 brokerage live trading are
  DEFERRED initiatives.  Every entry point declares its initiative; the guard
  judges the declaration and never infers scope from date ranges or payload
  shapes.  Declaring a deferred initiative raises unconditionally.
- Any brokerage credential/account/position/balance/order access is refused
  BEFORE any secret or resource resolution semantics can run.  The rejection
  event carries identity/hash/reason only — a suspected secret value is
  recorded as ``sha256`` first-12-hex, never in the clear.

Pure domain module: no ORM, API, scheduler, or vendor imports.

Matching precision (why legitimate system vocabulary survives):

- Matching is segment-based (non-alphanumeric and camelCase boundaries), so
  ``balanced`` does not match ``balance`` and ``recorder`` does not match
  ``order``.
- Documented benign identifiers stay allowed: ``sort_order`` (preset/skill
  ordering) and ``position_size``/``position_range`` (simulation sizing, not a
  brokerage position resource).
- Bare ``broker`` is market-data vocabulary in this system (``broker_recommend``,
  ``top_buy_broker``); it is denied only when co-occurring with a secret-shaped
  segment (``broker_token``, ``broker_api_key``).  ``brokerage`` and ``券商``
  have no legitimate use and always deny.
- String VALUES are scanned only when identifier-shaped (single ASCII token):
  prose such as frontend log messages can never trip the guard, while a value
  that names brokerage infrastructure (``"brokerage"``) still denies.
"""
from __future__ import annotations

import re
from enum import StrEnum
from hashlib import sha256
from typing import Any, Mapping

from app.governance.envelope import GovernanceError


DEFERRED_INITIATIVE_BLOCKED = "deferred_initiative_blocked"
BROKERAGE_ACCESS_DENIED = "brokerage_access_denied"
RISK_PORTFOLIO_KIND_DENIED = "risk_portfolio_kind_denied"

# Canonical declaration for allowed reconstruction entry points (e.g.
# scripts/replay_all_days.py).  The guard judges this declaration only; it
# never guesses scope from the requested date range.
HISTORICAL_RECONSTRUCTION_INITIATIVE = "historical_reconstruction"

# Risk_System boundary (reused by S6): only these portfolio kinds may ever
# feed risk evaluation.  Live/brokerage portfolios are out of scope.
ALLOWED_RISK_PORTFOLIO_KINDS = frozenset({"recommendation", "simulation", "paper", "shadow"})


class DeferredInitiative(StrEnum):
    D1_FULL_HISTORICAL_BACKFILL = "D1_FULL_HISTORICAL_BACKFILL"
    D2_NEW_ALPHA = "D2_NEW_ALPHA"
    D3_BROKERAGE_LIVE_TRADING = "D3_BROKERAGE_LIVE_TRADING"


class BrokerageResource(StrEnum):
    CREDENTIAL = "credential"
    ACCOUNT = "account"
    POSITION = "position"
    BALANCE = "balance"
    ORDER = "order"


class ScopeViolation(GovernanceError):
    """Fail-closed denial raised before any secret/resource resolution.

    ``event`` is the safe rejection record: identity, hashes, and reason only.
    It never contains a raw payload value, so it is safe to log or return.
    """

    def __init__(self, code: str, message: str, *, event: Mapping[str, Any] | None = None) -> None:
        self.event = dict(event) if event is not None else None
        super().__init__(code, message)


_SEGMENT_SPLIT_RE = re.compile(r"[^0-9A-Za-z]+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_VALUE_TOKEN_GATE_RE = re.compile(r"^[0-9A-Za-z_.:/+\-=]{1,256}$")

_SEGMENT_RESOURCE = {
    "credential": BrokerageResource.CREDENTIAL,
    "credentials": BrokerageResource.CREDENTIAL,
    "account": BrokerageResource.ACCOUNT,
    "accounts": BrokerageResource.ACCOUNT,
    "position": BrokerageResource.POSITION,
    "positions": BrokerageResource.POSITION,
    "balance": BrokerageResource.BALANCE,
    "balances": BrokerageResource.BALANCE,
    "order": BrokerageResource.ORDER,
    "orders": BrokerageResource.ORDER,
}
# Whole-identifier segment sequences with documented non-brokerage meaning.
_BENIGN_SEGMENT_SEQUENCES = frozenset(
    {
        ("sort", "order"),  # preset / skill ordering
        ("position", "size"),  # simulation sizing fraction
        ("position", "range"),  # agent review sizing advice
    }
)
_BROKER_SECRET_SEGMENTS = frozenset(
    {"token", "secret", "key", "password", "passwd", "credential", "credentials"}
)
_BROKERAGE_GENERIC = "brokerage"


def _segments(text: str) -> list[str]:
    pieces: list[str] = []
    for chunk in _SEGMENT_SPLIT_RE.split(text):
        pieces.extend(_CAMEL_BOUNDARY_RE.split(chunk))
    return [piece.lower() for piece in pieces if piece]


def _match_brokerage_resource(identifier: str) -> BrokerageResource | str | None:
    """Classify one identifier-shaped string; None means no brokerage marker."""

    if not identifier:
        return None
    if "券商" in identifier:
        return _BROKERAGE_GENERIC
    segments = _segments(identifier)
    if not segments or tuple(segments) in _BENIGN_SEGMENT_SEQUENCES:
        return None
    if "brokerage" in segments or "brokerages" in segments:
        return _BROKERAGE_GENERIC
    for segment in segments:
        resource = _SEGMENT_RESOURCE.get(segment)
        if resource is not None:
            return resource
    if "broker" in segments and any(segment in _BROKER_SECRET_SEGMENTS for segment in segments):
        return BrokerageResource.CREDENTIAL
    return None


def _secret_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:12]


def _brokerage_violation(
    *,
    resource: BrokerageResource | str,
    matched: str,
    context: str,
    key_path: str | None = None,
    suspected_secret: str | None = None,
) -> ScopeViolation:
    resource_label = resource.value if isinstance(resource, BrokerageResource) else str(resource)
    event: dict[str, Any] = {
        "event": "scope_guard.brokerage_access_denied",
        "code": BROKERAGE_ACCESS_DENIED,
        "resource": resource_label,
        "matched": matched,
        "context": str(context),
        "reason": "D3 券商实盘为延期范围；在任何 secret/resource 解析语义之前拒绝",
    }
    if key_path:
        # Key names are identifiers, not secrets — safe to record.
        event["key_path"] = key_path
    if isinstance(suspected_secret, str) and suspected_secret:
        event["value_sha256_12"] = _secret_hash(suspected_secret)
    location = f" at {key_path}" if key_path else ""
    label_part = "access" if resource_label == _BROKERAGE_GENERIC else f"{resource_label} access"
    message = (
        f"brokerage {label_part} {matched} refused{location}: "
        f"D3 券商实盘为延期范围，已在 secret 解析前拒绝"
    )
    return ScopeViolation(BROKERAGE_ACCESS_DENIED, message, event=event)


def _walk_payload(node: Any, path: str, *, context: str) -> None:
    if isinstance(node, Mapping):
        for key, nested in node.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            resource = _match_brokerage_resource(key_text)
            if resource is not None:
                suspected = nested if isinstance(nested, str) else None
                raise _brokerage_violation(
                    resource=resource,
                    matched="key",
                    context=context,
                    key_path=child_path,
                    suspected_secret=suspected,
                )
            _walk_payload(nested, child_path, context=context)
    elif isinstance(node, (list, tuple, set, frozenset)):
        for index, item in enumerate(node):
            _walk_payload(item, f"{path}[{index}]", context=context)
    elif isinstance(node, str):
        # Prose (whitespace / CJK free text) cannot be a resource handle and is
        # never scanned; only identifier-shaped values are matched.
        if _VALUE_TOKEN_GATE_RE.fullmatch(node):
            resource = _match_brokerage_resource(node)
            if resource is not None:
                raise _brokerage_violation(
                    resource=resource,
                    matched="value",
                    context=context,
                    key_path=path or None,
                    suspected_secret=node,
                )


def assert_initiative_allowed(initiative: object, *, context: str) -> None:
    """Fail-closed: declaring D1/D2/D3 always raises, regardless of anything else.

    Allowed initiatives (e.g. ``HISTORICAL_RECONSTRUCTION_INITIATIVE``) pass
    through unchanged — the guard judges the caller's declaration and never
    infers scope from date ranges or payload content.
    """

    member: DeferredInitiative | None = None
    if isinstance(initiative, DeferredInitiative):
        member = initiative
    elif isinstance(initiative, str):
        normalized = initiative.strip().upper()
        for candidate in DeferredInitiative:
            if normalized in (candidate.name.upper(), candidate.value.upper()):
                member = candidate
                break
    if member is None:
        return
    event = {
        "event": "scope_guard.deferred_initiative_blocked",
        "code": DEFERRED_INITIATIVE_BLOCKED,
        "initiative": member.value,
        "context": str(context),
        "reason": "延期范围 initiative 一律 fail-closed 拒绝",
    }
    raise ScopeViolation(
        DEFERRED_INITIATIVE_BLOCKED,
        f"initiative {member.value} 属延期范围，拒绝执行",
        event=event,
    )


def assert_no_brokerage_access(payload_or_keys: Any, *, context: str) -> None:
    """Fail-closed scan before any secret/resource resolution semantics.

    Accepts a request payload mapping (keys and nested keys scanned, string
    values scanned when identifier-shaped), a bare string, or a flat
    collection of strings (CLI arguments, config key names).  Raises
    ``ScopeViolation`` carrying the safe rejection event on the first match;
    returns None when nothing brokerage-shaped is present.
    """

    if payload_or_keys is None:
        return
    if isinstance(payload_or_keys, Mapping):
        _walk_payload(payload_or_keys, "", context=context)
        return
    if isinstance(payload_or_keys, str):
        tokens: tuple[str, ...] = (payload_or_keys,)
    elif (
        isinstance(payload_or_keys, (list, tuple, set, frozenset))
        and all(isinstance(item, str) for item in payload_or_keys)
    ):
        tokens = tuple(payload_or_keys)
    else:
        # Structured non-mapping payload (e.g. a JSON array body).
        _walk_payload(payload_or_keys, "", context=context)
        return
    for token in tokens:
        resource = _match_brokerage_resource(token.lstrip("-"))
        if resource is not None:
            raise _brokerage_violation(
                resource=resource,
                matched="token",
                context=context,
                suspected_secret=token,
            )


def assert_risk_portfolio_kind(kind: object, *, context: str = "") -> None:
    """Risk_System boundary: only ALLOWED_RISK_PORTFOLIO_KINDS may be evaluated."""

    normalized = kind.strip().lower() if isinstance(kind, str) else ""
    if normalized in ALLOWED_RISK_PORTFOLIO_KINDS:
        return
    event = {
        "event": "scope_guard.risk_portfolio_kind_denied",
        "code": RISK_PORTFOLIO_KIND_DENIED,
        "kind": str(kind),
        "context": str(context),
        "reason": "Risk_System 仅允许 recommendation/simulation/paper/shadow 组合",
    }
    raise ScopeViolation(
        RISK_PORTFOLIO_KIND_DENIED,
        f"risk portfolio kind {kind!r} 不在白名单内",
        event=event,
    )


__all__ = [
    "ALLOWED_RISK_PORTFOLIO_KINDS",
    "BROKERAGE_ACCESS_DENIED",
    "BrokerageResource",
    "DEFERRED_INITIATIVE_BLOCKED",
    "DeferredInitiative",
    "HISTORICAL_RECONSTRUCTION_INITIATIVE",
    "RISK_PORTFOLIO_KIND_DENIED",
    "ScopeViolation",
    "assert_initiative_allowed",
    "assert_no_brokerage_access",
    "assert_risk_portfolio_kind",
]
