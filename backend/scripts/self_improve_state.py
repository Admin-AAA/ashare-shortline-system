"""Locked, atomic persistence for the autonomous self-improve loop.

The state JSON and append-only run log are shared by every worktree.  Writers
must use this module so a concurrent or interrupted run cannot leave partial
JSON or interleaved JSONL records behind.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


DEFAULT_MEMORY_DIR = Path(
    "/Users/wangao/.claude/projects/-Users-wangao/memory"
)


def _memory_dir() -> Path:
    return Path(os.environ.get("SELF_IMPROVE_MEMORY_DIR", DEFAULT_MEMORY_DIR))


@contextmanager
def _locked(memory_dir: Path) -> Iterator[None]:
    memory_dir.mkdir(parents=True, exist_ok=True)
    lock_path = memory_dir / "self-improve-state.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            json.dump(value, temp_file, ensure_ascii=False, indent=2, sort_keys=True)
            temp_file.write("\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def read_state(memory_dir: Path | None = None) -> dict:
    directory = memory_dir or _memory_dir()
    with _locked(directory):
        return _read_json(directory / "self-improve-state.json")


def commit_update(
    state_patch: dict,
    run_record: dict | None = None,
    memory_dir: Path | None = None,
) -> dict:
    """Merge a top-level state patch and optionally append one run atomically."""

    if not isinstance(state_patch, dict):
        raise TypeError("state_patch must be a JSON object")
    if run_record is not None and not isinstance(run_record, dict):
        raise TypeError("run_record must be a JSON object")

    directory = memory_dir or _memory_dir()
    state_path = directory / "self-improve-state.json"
    runs_path = directory / "self-improve-runs.jsonl"
    with _locked(directory):
        state = _read_json(state_path)
        state.update(state_patch)
        _atomic_write_json(state_path, state)
        if run_record is not None:
            encoded = json.dumps(run_record, ensure_ascii=False, separators=(",", ":"))
            with runs_path.open("a", encoding="utf-8") as runs_file:
                runs_file.write(encoded + "\n")
                runs_file.flush()
                os.fsync(runs_file.fileno())
        return state


def _json_object(raw: str) -> dict:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("value must be a JSON object")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("read")
    commit_parser = subparsers.add_parser("commit")
    commit_parser.add_argument("--state-patch", required=True, type=_json_object)
    commit_parser.add_argument("--run-record", type=_json_object)
    args = parser.parse_args()

    if args.command == "read":
        result = read_state()
    else:
        result = commit_update(args.state_patch, args.run_record)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
