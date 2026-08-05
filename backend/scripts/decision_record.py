#!/usr/bin/env python3
"""CLI projection for immutable architecture decision records."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any

from app.governance.envelope import GovernanceError
from app.governance.store import GovernanceObjectStore
from app.services.decision_records import DecisionMetadata, DecisionRecordService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create and inspect immutable architecture decisions")
    parser.add_argument("--store-root", type=Path, help="governance store root (defaults to project store)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    _add_write_args(create)
    create.add_argument("--payload", type=Path, required=True)

    supersede = subparsers.add_parser("supersede")
    _add_write_args(supersede)
    supersede.add_argument("--previous", required=True, help="previous architecture_decision object ID")
    supersede.add_argument("--payload", type=Path, required=True)

    show = subparsers.add_parser("show")
    show.add_argument("object_id")

    verify = subparsers.add_parser("verify")
    verify.add_argument("object_id")

    listing = subparsers.add_parser("list")
    listing.add_argument("--decision-key")
    listing.add_argument("--status")

    render = subparsers.add_parser("render", aliases=["render-markdown"])
    render.add_argument("object_id")
    render.add_argument("--output", type=Path)
    return parser


def _add_write_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--actor", required=True)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--created-at", required=True, help="timezone-aware ISO-8601 timestamp")


def _metadata(args: argparse.Namespace) -> DecisionMetadata:
    raw = args.created_at.strip()
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GovernanceError("invalid_timestamp", "created-at must be ISO-8601") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise GovernanceError("invalid_timestamp", "created-at must include a timezone")
    return DecisionMetadata(args.actor, args.code_revision, value)


def _load_payload(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GovernanceError("payload_read_failed", str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise GovernanceError("payload_invalid_json", str(exc)) from exc
    if not isinstance(value, dict):
        raise GovernanceError("invalid_decision_payload", "payload JSON must be an object")
    return value


def _document(envelope: Any) -> dict[str, Any]:
    return envelope.to_document()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    service = DecisionRecordService(GovernanceObjectStore(args.store_root))
    try:
        if args.command == "create":
            envelope = service.create(_load_payload(args.payload), _metadata(args))
            print(json.dumps(_document(envelope), ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        if args.command == "supersede":
            envelope = service.supersede(args.previous, _load_payload(args.payload), _metadata(args))
            print(json.dumps(_document(envelope), ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        if args.command == "show":
            print(json.dumps(_document(service.show(args.object_id)), ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        if args.command == "verify":
            envelope = service.verify(args.object_id)
            print(json.dumps({"valid": True, "object_id": envelope.object_id, "kind": envelope.kind}, ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        if args.command == "list":
            records = service.list(decision_key=args.decision_key, status=args.status)
            print(json.dumps([_document(item) for item in records], ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        if args.command in {"render", "render-markdown"}:
            markdown = service.render_markdown(args.object_id)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(markdown, encoding="utf-8")
            else:
                sys.stdout.write(markdown)
            return 0
        raise GovernanceError("invalid_command", "unknown decision command")
    except GovernanceError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
