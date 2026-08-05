"""Append-only governance event log.

Every freeze, verification attempt, approval, admission failure, and state
transition appends one immutable event object plus one line in the ordered
``events.jsonl`` index.  The log is the audit spine: it records that capture
and freeze events happened *before* any behavior-changing work.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping

from app.governance.envelope import GovernanceError, ImmutableEnvelope
from app.governance.store import GovernanceObjectStore


EVENT_KIND = "governance_event"
_INDEX_FILENAME = "events.jsonl"


class GovernanceEventLog:
    """Ordered, append-only event feed backed by the object store."""

    def __init__(self, store: GovernanceObjectStore) -> None:
        self._store = store

    @property
    def _index_path(self) -> Path:
        return self._store.root / _INDEX_FILENAME

    def append(
        self,
        *,
        event_type: str,
        actor: str,
        code_revision: str,
        recorded_at: datetime,
        payload: Mapping[str, Any],
        referenced_object_ids: tuple[str, ...] = (),
    ) -> ImmutableEnvelope:
        if not isinstance(event_type, str) or not event_type.strip():
            raise GovernanceError("invalid_event_type", "event_type must be non-empty")
        envelope = self._store.put(
            kind=EVENT_KIND,
            payload={
                "actor": actor,
                "event_type": event_type,
                "payload": dict(payload),
            },
            created_at=recorded_at,
            created_by=actor,
            code_revision=code_revision,
            referenced_object_ids=referenced_object_ids,
        )
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        with self._index_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "event_type": event_type,
                        "object_id": envelope.object_id,
                        "recorded_at": envelope.to_document()["created_at"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
        return envelope

    def iter_events(self) -> Iterator[ImmutableEnvelope]:
        """Yield events in append order using the index file."""

        if not self._index_path.exists():
            return
        with self._index_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                yield self._store.get(entry["object_id"])

    def first_event_of_type(self, event_type: str) -> ImmutableEnvelope | None:
        for envelope in self.iter_events():
            if envelope.payload.get("event_type") == event_type:
                return envelope
        return None

    def count(self) -> int:
        return sum(1 for _ in self.iter_events())


__all__ = ["EVENT_KIND", "GovernanceEventLog"]
