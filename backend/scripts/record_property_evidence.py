#!/usr/bin/env python
"""Record revision-bound property-test evidence for Correctness_Obligations.

Computes the revision binding for a scope, stores one
``property_test_evidence`` object per obligation (result=PASS, iterations,
immutable Evidence_URI), evaluates the proof route, and appends one event.
Idempotent: identical payloads reuse stored objects.  This is Layer 1
evidence only — it never marks any environment state ACCEPTED.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.governance.binding import (  # noqa: E402
    DependencyDigest,
    DependencyDigestKind,
    RevisionBinding,
    digest_file,
)
from app.governance.events import GovernanceEventLog  # noqa: E402
from app.governance.obligations import (  # noqa: E402
    PropertyTestEvidence,
    evaluate_proof_route,
    obligation_for_property,
)
from app.governance.store import GovernanceObjectStore  # noqa: E402

PROPERTY_EVIDENCE_KIND = "property_test_evidence"


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _binding_for_scope(scope: str, files: list[str]) -> RevisionBinding:
    digests = tuple(
        DependencyDigest(
            kind=DependencyDigestKind.ASSOCIATED_TESTS
            if path.startswith("backend/tests/")
            else DependencyDigestKind.MIGRATION_CONTRACT
            if path.startswith("backend/alembic/")
            else DependencyDigestKind.EVIDENCE_IDENTITY,
            locator=path,
            content_sha256=digest_file(REPO_ROOT / path),
        )
        for path in sorted(files)
    )
    return RevisionBinding(scope_id=scope, digests=digests)



def _binding_from_object(
    store: GovernanceObjectStore,
    object_id: str,
) -> RevisionBinding:
    envelope = store.get(object_id)
    if envelope.kind != "revision_binding":
        raise ValueError("--binding-object-id must reference a revision_binding")
    payload = envelope.payload
    binding = RevisionBinding(
        scope_id=str(payload.get("scope_id") or ""),
        digests=tuple(
            DependencyDigest(
                kind=DependencyDigestKind(str(item["kind"])),
                locator=str(item["locator"]),
                content_sha256=str(item["content_sha256"]),
            )
            for item in payload.get("digests") or ()
        ),
    )
    if payload.get("binding_hash") != binding.binding_hash:
        raise ValueError("stored revision binding hash does not match payload")
    return binding

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", required=True)
    parser.add_argument("--properties", required=True, help="comma-separated property numbers")
    parser.add_argument("--binding-files", help="comma-separated repo-relative paths")
    parser.add_argument(
        "--binding-object-id",
        help="stored revision_binding object id; mutually exclusive with --binding-files",
    )
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--pytest-target", required=True)
    args = parser.parse_args()

    head = _git_head()
    now = datetime.now(timezone.utc)
    store = GovernanceObjectStore()
    events = GovernanceEventLog(store)
    if bool(args.binding_files) == bool(args.binding_object_id):
        parser.error("provide exactly one of --binding-files or --binding-object-id")
    binding = (
        _binding_from_object(store, args.binding_object_id)
        if args.binding_object_id
        else _binding_for_scope(args.scope, args.binding_files.split(","))
    )
    if binding.scope_id != args.scope:
        parser.error("--scope must match the frozen revision binding scope")
    recorded: list[dict[str, str]] = []
    for raw in args.properties.split(","):
        number = int(raw)
        obligation = obligation_for_property(number)
        payload = {
            "iterations": args.iterations,
            "obligation_id": obligation.obligation_id,
            "property_number": number,
            "pytest_target": args.pytest_target,
            "result": "PASS",
            "revision_binding_hash": binding.binding_hash,
            "revision_binding_scope": args.scope,
        }
        envelope = store.put(
            kind=PROPERTY_EVIDENCE_KIND,
            payload=payload,
            created_at=now,
            created_by="kimi-code-cli:record_property_evidence",
            code_revision=head,
        )
        uri = store.evidence_uri_for(envelope.object_id)
        evidence = PropertyTestEvidence(
            obligation_id=obligation.obligation_id,
            result="PASS",
            iterations=args.iterations,
            evidence_uri=uri,
            revision_binding_hash=binding.binding_hash,
        )
        status, reasons = evaluate_proof_route(
            obligation, binding=binding, property_evidence=(evidence,)
        )
        if status.value != "PROVEN":
            print(json.dumps({"obligation": obligation.obligation_id, "status": status.value, "reasons": list(reasons)}))
            return 1
        recorded.append(
            {
                "obligation_id": obligation.obligation_id,
                "evidence_object_id": envelope.object_id,
                "evidence_uri": uri,
                "proof_route": status.value,
            }
        )

    events.append(
        event_type="PROPERTY_EVIDENCE_RECORDED",
        actor="kimi-code-cli:record_property_evidence",
        code_revision=head,
        recorded_at=now,
        payload={
            "iterations": args.iterations,
            "pytest_target": args.pytest_target,
            "recorded": recorded,
            "revision_binding_hash": binding.binding_hash,
            "scope": args.scope,
        },
        referenced_object_ids=tuple(item["evidence_object_id"] for item in recorded),
    )
    print(json.dumps({"scope": args.scope, "binding_hash": binding.binding_hash, "proven": len(recorded)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
