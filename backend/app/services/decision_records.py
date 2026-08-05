"""Versioned architecture decision records backed by immutable governance objects.

The object store is the authority.  ``decision_index.jsonl`` and Markdown are
only validated projections used to find and display records; neither can
change a decision.  Every write is serialized by a process-safe file lock so
identity conflicts fail closed rather than overwriting a prior record.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from app.governance.envelope import GovernanceError, ImmutableEnvelope
from app.governance.events import GovernanceEventLog
from app.governance.locking import file_lock
from app.governance.store import GovernanceObjectStore


DECISION_KIND = "architecture_decision"
DECISION_INDEX_FILENAME = "decision_index.jsonl"
DECISION_EVENT_CREATED = "architecture_decision_created"
DECISION_EVENT_SUPERSEDED = "architecture_decision_superseded"

_MAX_DECISION_KEY = 256
_MAX_TITLE = 512
_MAX_CONTEXT = 20_000
_MAX_TEXT = 8_000
_MAX_REFERENCE = 2_048
_MAX_LIST_ITEMS = 100


class DecisionStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


class BehaviorActivationState(StrEnum):
    NOT_ACTIVATED = "not_activated"
    SHADOW_ONLY = "shadow_only"
    ACTIVE = "active"
    BLOCKED = "blocked"
    RETIRED = "retired"


class DecisionReferenceType(StrEnum):
    OBJECT_ID = "object_id"
    EVIDENCE_URI = "evidence_uri"
    EXTERNAL = "external"


_VALIDATION_STATUSES = frozenset({"passed", "failed", "blocked", "not_run"})
_OPTION_DISPOSITIONS = frozenset({"adopted", "rejected", "deferred", "considered"})
_SEVERITIES = frozenset({"low", "medium", "high", "critical"})


def _fail(code: str, message: str) -> GovernanceError:
    return GovernanceError(code, message)


def _text(value: object, field: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail("invalid_decision_payload", f"{field} must be a non-empty string")
    value = value.strip()
    if len(value) > max_length:
        raise _fail(
            "decision_field_too_long",
            f"{field} exceeds maximum length {max_length}",
        )
    return value


def _optional_text(value: object, field: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    return _text(value, field, max_length=max_length)


def _sequence(value: object, field: str, *, required: bool = False) -> list[Any]:
    if value is None:
        values: list[Any] = []
    elif isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Iterable):
        raise _fail("invalid_decision_payload", f"{field} must be a list")
    else:
        values = list(value)
    if len(values) > _MAX_LIST_ITEMS:
        raise _fail("decision_list_too_long", f"{field} has more than {_MAX_LIST_ITEMS} items")
    if required and not values:
        raise _fail("decision_evidence_required", f"accepted decisions require {field}")
    return values


def _normalise_reference_type(value: object) -> DecisionReferenceType:
    if isinstance(value, DecisionReferenceType):
        return value
    aliases = {
        "object": DecisionReferenceType.OBJECT_ID,
        "object_id": DecisionReferenceType.OBJECT_ID,
        "evidence": DecisionReferenceType.EVIDENCE_URI,
        "evidence_uri": DecisionReferenceType.EVIDENCE_URI,
        "external": DecisionReferenceType.EXTERNAL,
        "url": DecisionReferenceType.EXTERNAL,
    }
    try:
        return aliases[str(value).strip().lower()]
    except KeyError as exc:
        raise _fail(
            "invalid_decision_reference",
            "reference_type must be object_id, evidence_uri, or external",
        ) from exc


@dataclass(frozen=True, slots=True)
class DecisionReference:
    """A typed reference; external references are explicitly non-hash-backed."""

    reference_type: DecisionReferenceType
    value: str
    label: str | None = None
    revision: str | None = None

    @classmethod
    def from_value(cls, value: object) -> "DecisionReference":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            if value.startswith("evidence://sha256/"):
                ref_type = DecisionReferenceType.EVIDENCE_URI
            elif re.fullmatch(r"[a-z][a-z0-9_]{0,63}:sha256:[0-9a-f]{64}", value):
                ref_type = DecisionReferenceType.OBJECT_ID
            else:
                ref_type = DecisionReferenceType.EXTERNAL
            return cls(ref_type, value)
        if not isinstance(value, Mapping):
            raise _fail("invalid_decision_reference", "reference must be a string or object")
        raw_type = value.get("reference_type", value.get("type", value.get("kind")))
        raw_value = value.get("value", value.get("uri", value.get("object_id")))
        if raw_type is None and isinstance(raw_value, str):
            return cls.from_value(raw_value)
        ref_type = _normalise_reference_type(raw_type)
        text_value = _text(raw_value, "reference.value", max_length=_MAX_REFERENCE)
        label = _optional_text(value.get("label"), "reference.label", max_length=512)
        revision = _optional_text(value.get("revision"), "reference.revision", max_length=256)
        if ref_type is DecisionReferenceType.OBJECT_ID and not re.fullmatch(
            r"[a-z][a-z0-9_]{0,63}:sha256:[0-9a-f]{64}", text_value
        ):
            raise _fail("invalid_decision_reference", "object_id reference has invalid identity")
        if ref_type is DecisionReferenceType.EVIDENCE_URI and not text_value.startswith(
            "evidence://sha256/"
        ):
            raise _fail("invalid_decision_reference", "evidence_uri reference has invalid URI")
        if ref_type is DecisionReferenceType.EXTERNAL:
            parsed = urlparse(text_value)
            if not (parsed.scheme or text_value.startswith("repo://")):
                raise _fail(
                    "invalid_decision_reference",
                    "external reference must identify a URI or repo:// locator",
                )
        return cls(ref_type, text_value, label, revision)

    def to_dict(self) -> dict[str, str]:
        ref_type = self.reference_type.value if isinstance(self.reference_type, DecisionReferenceType) else str(self.reference_type)
        result = {"reference_type": ref_type, "value": self.value}
        if self.label is not None:
            result["label"] = self.label
        if self.revision is not None:
            result["revision"] = self.revision
        return result


@dataclass(frozen=True, slots=True)
class DecisionOption:
    name: str
    description: str
    disposition: str = "considered"

    @classmethod
    def from_value(cls, value: object) -> "DecisionOption":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise _fail("invalid_decision_option", "option must be an object")
        name = _text(value.get("name", value.get("option")), "option.name", max_length=256)
        description = _text(
            value.get("description", value.get("rationale", name)),
            "option.description",
            max_length=_MAX_TEXT,
        )
        disposition = str(value.get("disposition", "considered")).strip().lower()
        if disposition not in _OPTION_DISPOSITIONS:
            raise _fail("invalid_decision_option", "option.disposition is not recognised")
        return cls(name, description, disposition)

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "description": self.description,
            "disposition": self.disposition,
        }


@dataclass(frozen=True, slots=True)
class DecisionValidation:
    command: str
    status: str
    summary: str
    evidence_references: tuple[DecisionReference, ...] = ()

    @classmethod
    def from_value(cls, value: object) -> "DecisionValidation":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise _fail("invalid_decision_validation", "validation must be an object")
        command = _text(value.get("command"), "validation.command", max_length=2_000)
        status = str(value.get("status", value.get("result", "passed"))).strip().lower()
        if isinstance(value.get("result"), Mapping):
            result = value["result"]
            status = str(result.get("status", status)).strip().lower()
            summary_value = value.get("summary", result.get("summary"))
        else:
            summary_value = value.get("summary", value.get("details", ""))
        if status not in _VALIDATION_STATUSES:
            raise _fail("invalid_decision_validation", "validation.status is not recognised")
        summary = _text(summary_value, "validation.summary", max_length=_MAX_TEXT)
        refs = tuple(
            DecisionReference.from_value(item)
            for item in _sequence(
                value.get("evidence_references", value.get("evidence", ())),
                "validation.evidence_references",
            )
        )
        return cls(command, status, summary, refs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "status": self.status,
            "summary": self.summary,
            "evidence_references": [DecisionReference.from_value(item).to_dict() for item in self.evidence_references],
        }


@dataclass(frozen=True, slots=True)
class DecisionRisk:
    risk: str
    mitigation: str
    severity: str = "medium"

    @classmethod
    def from_value(cls, value: object) -> "DecisionRisk":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise _fail("invalid_decision_risk", "risk must be an object")
        risk = _text(value.get("risk", value.get("description")), "risk.risk", max_length=_MAX_TEXT)
        mitigation = _text(value.get("mitigation"), "risk.mitigation", max_length=_MAX_TEXT)
        severity = str(value.get("severity", "medium")).strip().lower()
        if severity not in _SEVERITIES:
            raise _fail("invalid_decision_risk", "risk.severity is not recognised")
        return cls(risk, mitigation, severity)

    def to_dict(self) -> dict[str, str]:
        return {"risk": self.risk, "mitigation": self.mitigation, "severity": self.severity}


@dataclass(frozen=True, slots=True)
class ArchitectureDecisionPayload:
    decision_key: str
    title: str
    status: DecisionStatus
    context: str
    source_references: tuple[DecisionReference, ...] = ()
    options: tuple[DecisionOption, ...] = ()
    adopted_scope: tuple[str, ...] = ()
    rejected_scope: tuple[str, ...] = ()
    rationale: tuple[str, ...] = ()
    tradeoffs: tuple[str, ...] = ()
    evidence_references: tuple[DecisionReference, ...] = ()
    validations: tuple[DecisionValidation, ...] = ()
    risks: tuple[DecisionRisk, ...] = ()
    next_state: str = ""
    related_requirement_ids: tuple[str, ...] = ()
    behavior_activation_state: BehaviorActivationState = BehaviorActivationState.NOT_ACTIVATED

    @classmethod
    def from_value(cls, value: "ArchitectureDecisionPayload | Mapping[str, Any]") -> "ArchitectureDecisionPayload":
        if isinstance(value, cls):
            value = value.to_dict()
        if not isinstance(value, Mapping):
            raise _fail("invalid_decision_payload", "decision payload must be an object")
        allowed = {
            "decision_key", "title", "status", "context", "source_references", "source_refs",
            "options", "adopted_scope", "rejected_scope", "rationale", "tradeoffs",
            "evidence_references", "evidence_refs", "evidence", "validations", "validation",
            "risks", "next_state", "related_requirement_ids", "behavior_activation_state",
            "activation_state",
        }
        unknown = set(value) - allowed
        if unknown:
            raise _fail("unknown_decision_field", f"unknown decision field(s): {sorted(unknown)}")
        raw_status = value.get("status")
        try:
            status = raw_status if isinstance(raw_status, DecisionStatus) else DecisionStatus(str(raw_status).strip().lower())
        except ValueError as exc:
            raise _fail("invalid_decision_status", "status is not recognised") from exc
        raw_activation = value.get("behavior_activation_state", value.get("activation_state", BehaviorActivationState.NOT_ACTIVATED))
        try:
            activation = raw_activation if isinstance(raw_activation, BehaviorActivationState) else BehaviorActivationState(str(raw_activation).strip().lower())
        except ValueError as exc:
            raise _fail("invalid_activation_state", "behavior_activation_state is not recognised") from exc

        source_values = _sequence(value.get("source_references", value.get("source_refs", ())), "source_references")
        evidence_values = _sequence(value.get("evidence_references", value.get("evidence_refs", value.get("evidence", ()))), "evidence_references")
        validation_values = _sequence(value.get("validations", value.get("validation", ())), "validations")
        raw_rationale = _sequence(value.get("rationale", ()), "rationale")
        if status is DecisionStatus.ACCEPTED and not raw_rationale:
            raise _fail("decision_rationale_required", "accepted decisions require rationale")
        rationale_values = tuple(_text(item, "rationale", max_length=_MAX_TEXT) for item in raw_rationale)
        evidence = tuple(DecisionReference.from_value(item) for item in evidence_values)
        validations = tuple(DecisionValidation.from_value(item) for item in validation_values)
        if status is DecisionStatus.ACCEPTED:
            if not evidence:
                raise _fail("decision_evidence_required", "accepted decisions require evidence_references")
            if not validations:
                raise _fail("decision_validation_required", "accepted decisions require validations")
        return cls(
            decision_key=_text(value.get("decision_key"), "decision_key", max_length=_MAX_DECISION_KEY),
            title=_text(value.get("title"), "title", max_length=_MAX_TITLE),
            status=status,
            context=_text(value.get("context"), "context", max_length=_MAX_CONTEXT),
            source_references=tuple(DecisionReference.from_value(item) for item in source_values),
            options=tuple(DecisionOption.from_value(item) for item in _sequence(value.get("options", ()), "options")),
            adopted_scope=tuple(_text(item, "adopted_scope", max_length=_MAX_TEXT) for item in _sequence(value.get("adopted_scope", ()), "adopted_scope")),
            rejected_scope=tuple(_text(item, "rejected_scope", max_length=_MAX_TEXT) for item in _sequence(value.get("rejected_scope", ()), "rejected_scope")),
            rationale=rationale_values,
            tradeoffs=tuple(_text(item, "tradeoffs", max_length=_MAX_TEXT) for item in _sequence(value.get("tradeoffs", ()), "tradeoffs")),
            evidence_references=evidence,
            validations=validations,
            risks=tuple(DecisionRisk.from_value(item) for item in _sequence(value.get("risks", ()), "risks")),
            next_state=_text(value.get("next_state", "not specified"), "next_state", max_length=_MAX_TEXT),
            related_requirement_ids=tuple(_text(item, "related_requirement_ids", max_length=256) for item in _sequence(value.get("related_requirement_ids", ()), "related_requirement_ids")),
            behavior_activation_state=activation,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_key": self.decision_key,
            "title": self.title,
            "status": self.status.value if isinstance(self.status, DecisionStatus) else str(self.status),
            "context": self.context,
            "source_references": [DecisionReference.from_value(item).to_dict() for item in self.source_references],
            "options": [DecisionOption.from_value(item).to_dict() for item in self.options],
            "adopted_scope": list(self.adopted_scope),
            "rejected_scope": list(self.rejected_scope),
            "rationale": list(self.rationale),
            "tradeoffs": list(self.tradeoffs),
            "evidence_references": [DecisionReference.from_value(item).to_dict() for item in self.evidence_references],
            "validations": [DecisionValidation.from_value(item).to_dict() for item in self.validations],
            "risks": [DecisionRisk.from_value(item).to_dict() for item in self.risks],
            "next_state": self.next_state,
            "related_requirement_ids": list(self.related_requirement_ids),
            "behavior_activation_state": self.behavior_activation_state.value if isinstance(self.behavior_activation_state, BehaviorActivationState) else str(self.behavior_activation_state),
        }


@dataclass(frozen=True, slots=True)
class DecisionMetadata:
    actor: str
    code_revision: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.created_at, datetime) or self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise _fail("invalid_timestamp", "created_at must be timezone-aware")
        _text(self.actor, "actor", max_length=256)
        _text(self.code_revision, "code_revision", max_length=256)


class DecisionRecordService:
    """Create and query immutable architecture decisions."""

    def __init__(self, store: GovernanceObjectStore | None = None, event_log: GovernanceEventLog | None = None) -> None:
        self.store = store or GovernanceObjectStore()
        self.event_log = event_log or GovernanceEventLog(self.store)

    @property
    def index_path(self) -> Path:
        return self.store.root / DECISION_INDEX_FILENAME

    def create(
        self,
        payload: ArchitectureDecisionPayload | Mapping[str, Any],
        metadata: DecisionMetadata | Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        code_revision: str | None = None,
        created_at: datetime | None = None,
        recorded_at: datetime | None = None,
    ) -> ImmutableEnvelope:
        decision = ArchitectureDecisionPayload.from_value(payload)
        if created_at is not None and recorded_at is not None:
            raise _fail("duplicate_metadata", "use created_at or recorded_at, not both")
        meta = _coerce_metadata(metadata, actor=actor, code_revision=code_revision, created_at=created_at or recorded_at)
        with file_lock(self.index_path.with_suffix(".lock")):
            existing = self._stored_for_key(decision.decision_key)
            heads = self._heads(existing)
            if len(heads) > 1:
                raise _fail("decision_branch_conflict", "decision key has multiple active heads")
            if heads:
                current = ArchitectureDecisionPayload.from_value(heads[0].payload)
                if current.to_dict() == decision.to_dict():
                    self._append_index(heads[0], current)
                    return heads[0]
                raise _fail(
                    "decision_identity_conflict",
                    f"decision_key {decision.decision_key!r} already has a different payload; use supersede",
                )
            reference_ids = self._resolve_references(decision)
            envelope = self.store.put(
                kind=DECISION_KIND,
                payload=decision.to_dict(),
                created_at=meta.created_at,
                created_by=meta.actor,
                code_revision=meta.code_revision,
                referenced_object_ids=reference_ids,
            )
            self._append_index(envelope, decision)
        self._append_event(envelope, meta, DECISION_EVENT_CREATED)
        return envelope

    def supersede(
        self,
        previous_object_id: str,
        payload: ArchitectureDecisionPayload | Mapping[str, Any],
        metadata: DecisionMetadata | Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        code_revision: str | None = None,
        created_at: datetime | None = None,
        recorded_at: datetime | None = None,
    ) -> ImmutableEnvelope:
        previous = self.store.get(previous_object_id)
        if previous.kind != DECISION_KIND:
            raise _fail("invalid_supersedes_kind", "previous object is not an architecture decision")
        decision = ArchitectureDecisionPayload.from_value(payload)
        previous_decision = ArchitectureDecisionPayload.from_value(previous.payload)
        if decision.decision_key != previous_decision.decision_key:
            raise _fail("decision_key_mismatch", "superseding decision must keep the same decision_key")
        if created_at is not None and recorded_at is not None:
            raise _fail("duplicate_metadata", "use created_at or recorded_at, not both")
        meta = _coerce_metadata(metadata, actor=actor, code_revision=code_revision, created_at=created_at or recorded_at)
        with file_lock(self.index_path.with_suffix(".lock")):
            current = self._stored_for_key(decision.decision_key)
            for envelope in current:
                if envelope.supersedes_id != previous.object_id:
                    continue
                candidate = ArchitectureDecisionPayload.from_value(envelope.payload)
                if candidate.to_dict() == decision.to_dict():
                    return envelope
                raise _fail(
                    "decision_identity_conflict",
                    "this previous decision already has a different superseding payload",
                )
            heads = self._heads(current)
            if len(heads) > 1:
                raise _fail("decision_branch_conflict", "decision key has multiple active heads")
            if not heads or heads[0].object_id != previous.object_id:
                raise _fail("supersedes_not_current", "previous decision is not the current active head")
            envelope = self.store.put(
                kind=DECISION_KIND,
                payload=decision.to_dict(),
                created_at=meta.created_at,
                created_by=meta.actor,
                code_revision=meta.code_revision,
                supersedes_id=previous.object_id,
                referenced_object_ids=self._resolve_references(decision),
            )
            self._append_index(envelope, decision)
        self._append_event(envelope, meta, DECISION_EVENT_SUPERSEDED, previous_object_id)
        return envelope

    def show(self, object_id: str) -> ImmutableEnvelope:
        return self.verify(object_id)

    def get(self, object_id: str) -> ImmutableEnvelope:
        return self.show(object_id)

    def verify(self, object_id: str) -> ImmutableEnvelope:
        envelope = self.store.get(object_id)
        if envelope.kind != DECISION_KIND:
            raise _fail("invalid_decision_kind", "object is not an architecture decision")
        decision = ArchitectureDecisionPayload.from_value(envelope.payload)
        for reference in _all_references(decision):
            if reference.reference_type is DecisionReferenceType.OBJECT_ID:
                self.store.get(reference.value)
                if reference.value not in envelope.referenced_object_ids:
                    raise _fail("decision_reference_mismatch", "object reference is not recorded in envelope references")
            elif reference.reference_type is DecisionReferenceType.EVIDENCE_URI:
                resolved = self.store.resolve_evidence_uri(reference.value)
                if resolved.object_id not in envelope.referenced_object_ids:
                    raise _fail("decision_reference_mismatch", "evidence URI is not recorded in envelope references")
        if envelope.supersedes_id:
            previous = self.store.get(envelope.supersedes_id)
            if previous.kind != DECISION_KIND:
                raise _fail("invalid_supersedes_kind", "supersedes target is not an architecture decision")
            previous_payload = ArchitectureDecisionPayload.from_value(previous.payload)
            if previous_payload.decision_key != decision.decision_key:
                raise _fail("decision_key_mismatch", "supersedes target has a different decision_key")
        return envelope

    def list(
        self,
        *,
        decision_key: str | None = None,
        status: DecisionStatus | str | None = None,
    ) -> tuple[ImmutableEnvelope, ...]:
        if status is not None:
            try:
                wanted_status = status if isinstance(status, DecisionStatus) else DecisionStatus(str(status).lower())
            except ValueError as exc:
                raise _fail("invalid_decision_status", "status is not recognised") from exc
        else:
            wanted_status = None
        result: list[ImmutableEnvelope] = []
        for envelope in self._read_index():
            verified = self.verify(envelope.object_id)
            decision = ArchitectureDecisionPayload.from_value(verified.payload)
            if decision_key is not None and decision.decision_key != decision_key:
                continue
            if wanted_status is not None and decision.status is not wanted_status:
                continue
            result.append(verified)
        return tuple(result)

    def render_markdown(self, object_id: str) -> str:
        envelope = self.verify(object_id)
        decision = ArchitectureDecisionPayload.from_value(envelope.payload)
        lines = [
            f"# {decision.title}",
            "",
            f"- Decision key: `{decision.decision_key}`",
            f"- Status: `{decision.status.value}`",
            f"- Behavior activation: `{decision.behavior_activation_state.value}`",
            f"- Object ID: `{envelope.object_id}`",
            f"- Created: `{envelope.created_at.isoformat()}` by `{envelope.created_by}`",
            f"- Code revision: `{envelope.code_revision}`",
            "",
            "## Context",
            "",
            decision.context,
            "",
        ]
        _markdown_list(lines, "Adopted scope", decision.adopted_scope)
        _markdown_list(lines, "Rejected or deferred scope", decision.rejected_scope)
        _markdown_list(lines, "Rationale", decision.rationale)
        _markdown_list(lines, "Trade-offs", decision.tradeoffs)
        if decision.options:
            lines.extend(["## Options", ""])
            for option in decision.options:
                lines.append(f"- **{option.name}** ({option.disposition}): {option.description}")
            lines.append("")
        if decision.evidence_references:
            lines.extend(["## Evidence", ""])
            for reference in decision.evidence_references:
                lines.append(f"- `{reference.reference_type.value}`: `{reference.value}`")
            lines.append("")
        if decision.validations:
            lines.extend(["## Validation", ""])
            for validation in decision.validations:
                lines.append(f"- `{validation.status}` `{validation.command}` — {validation.summary}")
            lines.append("")
        if decision.risks:
            lines.extend(["## Risks and mitigations", ""])
            for risk in decision.risks:
                lines.append(f"- **{risk.severity}** {risk.risk} — {risk.mitigation}")
            lines.append("")
        if decision.source_references:
            lines.extend(["## Sources", ""])
            for reference in decision.source_references:
                lines.append(f"- `{reference.reference_type.value}`: `{reference.value}`")
            lines.append("")
        lines.extend(["## Next state", "", decision.next_state, ""])
        if decision.related_requirement_ids:
            lines.extend(["## Related requirements/tasks", "", *[f"- `{item}`" for item in decision.related_requirement_ids], ""])
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _heads(envelopes: list[ImmutableEnvelope]) -> list[ImmutableEnvelope]:
        superseded_ids = {item.supersedes_id for item in envelopes if item.supersedes_id}
        return [item for item in envelopes if item.object_id not in superseded_ids]

    def _stored_for_key(self, decision_key: str) -> list[ImmutableEnvelope]:
        result: list[ImmutableEnvelope] = []
        for envelope in self.store.iter_kind(DECISION_KIND):
            decision = ArchitectureDecisionPayload.from_value(envelope.payload)
            if decision.decision_key == decision_key:
                result.append(envelope)
        return result

    def _indexed_for_key(self, decision_key: str) -> list[ImmutableEnvelope]:
        return [item for item in self._read_index() if ArchitectureDecisionPayload.from_value(item.payload).decision_key == decision_key]

    def _read_index(self) -> list[ImmutableEnvelope]:
        entries = self._read_index_entries()
        envelopes = []
        for entry in entries:
            object_id = entry.get("object_id")
            if not isinstance(object_id, str):
                raise _fail("corrupt_decision_index", "decision index entry has no object_id")
            envelope = self.store.get(object_id)
            if envelope.kind != DECISION_KIND:
                raise _fail("corrupt_decision_index", "decision index points to a non-decision object")
            decision = ArchitectureDecisionPayload.from_value(envelope.payload)
            if entry.get("decision_key") != decision.decision_key or entry.get("status") != decision.status.value:
                raise _fail("corrupt_decision_index", "decision index disagrees with immutable object")
            if entry.get("supersedes_id") != envelope.supersedes_id:
                raise _fail("corrupt_decision_index", "decision index supersedes_id disagrees with immutable object")
            envelopes.append(envelope)
        return envelopes

    def _read_index_entries(self, *, locked: bool = True) -> list[dict[str, Any]]:
        if locked:
            with file_lock(self.index_path.with_suffix(".lock"), shared=True):
                return self._read_index_entries(locked=False)
        if not self.index_path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with self.index_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise _fail("corrupt_decision_index", "decision index contains invalid JSON") from exc
                if not isinstance(value, dict):
                    raise _fail("corrupt_decision_index", "decision index entry must be an object")
                entries.append(value)
        return entries

    def _resolve_references(self, decision: ArchitectureDecisionPayload) -> tuple[str, ...]:
        object_ids: set[str] = set()
        for reference in _all_references(decision):
            if reference.reference_type is DecisionReferenceType.OBJECT_ID:
                self.store.get(reference.value)
                object_ids.add(reference.value)
            elif reference.reference_type is DecisionReferenceType.EVIDENCE_URI:
                resolved = self.store.resolve_evidence_uri(reference.value)
                object_ids.add(resolved.object_id)
        return tuple(sorted(object_ids))

    def _append_index(self, envelope: ImmutableEnvelope, decision: ArchitectureDecisionPayload) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        existing_ids = {entry.get("object_id") for entry in self._read_index_entries(locked=False)}
        if envelope.object_id in existing_ids:
            return
        entry = {
            "object_id": envelope.object_id,
            "decision_key": decision.decision_key,
            "status": decision.status.value,
            "created_at": envelope.to_document()["created_at"],
            "supersedes_id": envelope.supersedes_id,
        }
        encoded = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        with self.index_path.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _append_event(
        self,
        envelope: ImmutableEnvelope,
        metadata: DecisionMetadata,
        event_type: str,
        previous_object_id: str | None = None,
    ) -> None:
        decision = ArchitectureDecisionPayload.from_value(envelope.payload)
        payload: dict[str, Any] = {"decision_key": decision.decision_key, "status": decision.status.value}
        if previous_object_id is not None:
            payload["previous_object_id"] = previous_object_id
        self.event_log.append(
            event_type=event_type,
            actor=metadata.actor,
            code_revision=metadata.code_revision,
            recorded_at=metadata.created_at,
            payload=payload,
            referenced_object_ids=(envelope.object_id,) + ((previous_object_id,) if previous_object_id else ()),
        )


def _coerce_metadata(
    metadata: DecisionMetadata | Mapping[str, Any] | None,
    *,
    actor: str | None,
    code_revision: str | None,
    created_at: datetime | None,
) -> DecisionMetadata:
    if metadata is not None:
        if isinstance(metadata, DecisionMetadata):
            if any(value is not None for value in (actor, code_revision, created_at)):
                raise _fail("duplicate_metadata", "use metadata or explicit write metadata, not both")
            return metadata
        if not isinstance(metadata, Mapping):
            raise _fail("invalid_metadata", "metadata must be an object")
        actor = metadata.get("actor", metadata.get("created_by", actor))
        code_revision = metadata.get("code_revision", code_revision)
        created_at = metadata.get("created_at", metadata.get("recorded_at", created_at))
    if actor is None or code_revision is None or created_at is None:
        raise _fail("write_metadata_required", "actor, code_revision, and aware created_at are required")
    return DecisionMetadata(actor=actor, code_revision=code_revision, created_at=created_at)


def _all_references(decision: ArchitectureDecisionPayload) -> tuple[DecisionReference, ...]:
    nested = tuple(reference for validation in decision.validations for reference in validation.evidence_references)
    return decision.source_references + decision.evidence_references + nested


def _markdown_list(lines: list[str], heading: str, values: tuple[str, ...]) -> None:
    if values:
        lines.extend([f"## {heading}", "", *[f"- {value}" for value in values], ""])


__all__ = [
    "ArchitectureDecisionPayload",
    "BehaviorActivationState",
    "DECISION_EVENT_CREATED",
    "DECISION_EVENT_SUPERSEDED",
    "DECISION_INDEX_FILENAME",
    "DECISION_KIND",
    "DecisionMetadata",
    "DecisionOption",
    "DecisionRecordService",
    "DecisionReference",
    "DecisionReferenceType",
    "DecisionRisk",
    "DecisionStatus",
    "DecisionValidation",
]
