"""Append-only governance event log.

Every freeze, verification attempt, approval, admission failure, and state
transition appends one immutable event object plus one line in the ordered
``events.jsonl`` index.  The log is the audit spine: it records that capture
and freeze events happened *before* any behavior-changing work.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping

from app.governance.envelope import GovernanceError, ImmutableEnvelope
from app.governance.locking import file_lock
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
        entry = json.dumps(
            {
                "event_type": event_type,
                "object_id": envelope.object_id,
                "recorded_at": envelope.to_document()["created_at"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        lock_path = self._index_path.with_suffix(".lock")
        with file_lock(lock_path):
            indexed_ids: set[str] = set()
            if self._index_path.exists():
                with self._index_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            indexed = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise GovernanceError(
                                "corrupt_event_index",
                                "governance event index contains invalid JSON",
                            ) from exc
                        object_id = indexed.get("object_id")
                        if not isinstance(object_id, str):
                            raise GovernanceError(
                                "corrupt_event_index",
                                "governance event index entry has no object_id",
                            )
                        indexed_ids.add(object_id)
            if envelope.object_id not in indexed_ids:
                with self._index_path.open("a", encoding="utf-8") as handle:
                    handle.write(entry + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
        return envelope

    def iter_events(self) -> Iterator[ImmutableEnvelope]:
        """Yield events in append order using the index file."""

        if not self._index_path.exists():
            return
        object_ids: list[str] = []
        with file_lock(self._index_path.with_suffix(".lock"), shared=True):
            with self._index_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise GovernanceError(
                            "corrupt_event_index",
                            "governance event index contains invalid JSON",
                        ) from exc
                    object_id = entry.get("object_id")
                    if not isinstance(object_id, str):
                        raise GovernanceError(
                            "corrupt_event_index",
                            "governance event index entry has no object_id",
                        )
                    object_ids.append(object_id)
        for object_id in object_ids:
            yield self._store.get(object_id)

    def first_event_of_type(self, event_type: str) -> ImmutableEnvelope | None:
        for envelope in self.iter_events():
            if envelope.payload.get("event_type") == event_type:
                return envelope
        return None

    def count(self) -> int:
        return sum(1 for _ in self.iter_events())


__all__ = ["EVENT_KIND", "GovernanceEventLog"]
