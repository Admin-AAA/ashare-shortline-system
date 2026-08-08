"""Validate the on-disk governance object store layout.

Governance objects are immutable, content-addressed JSON records.  This check
keeps runtime locks and accidental binary/temporary files out of the tracked
evidence tree without imposing an arbitrary limit on the number of valid
records.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


OBJECT_ID = re.compile(r"^[0-9a-f]{64}\.json$")
MAX_OBJECT_BYTES = 256 * 1024
INDEX_FILES = {"decision_index.jsonl", "events.jsonl"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def check_store(store: Path) -> list[str]:
    errors: list[str] = []
    if not store.is_dir():
        return [f"governance store does not exist: {store}"]

    json_count = 0
    for path in sorted(store.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(store)
        if path.name.endswith(".lock"):
            continue
        if path.suffix == ".json":
            json_count += 1
            if len(relative.parts) != 2 or not OBJECT_ID.fullmatch(path.name):
                errors.append(f"invalid object path/name: {relative}")
                continue
            if path.stat().st_size > MAX_OBJECT_BYTES:
                errors.append(
                    f"object exceeds {MAX_OBJECT_BYTES} bytes: {relative} ({path.stat().st_size})"
                )
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                errors.append(f"invalid JSON object {relative}: {exc}")
            continue
        if path.suffix == ".jsonl" and path.name in INDEX_FILES and len(relative.parts) == 1:
            try:
                for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if line.strip():
                        json.loads(line)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                errors.append(f"invalid JSONL projection {relative}: {exc}")
            continue
        errors.append(f"unexpected governance store file: {relative}")

    if json_count == 0:
        errors.append("governance store contains no immutable JSON objects")
    return errors


def main() -> int:
    store = _repo_root() / ".kiro/specs/production-evidence-risk-hardening/governance/store"
    errors = check_store(store)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    count = sum(1 for path in store.rglob("*.json") if path.is_file())
    print(f"governance store layout valid: {count} immutable JSON objects")
    return 0


if __name__ == "__main__":
    sys.exit(main())
