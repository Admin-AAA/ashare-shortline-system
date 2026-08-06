#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from app.db.session import SessionLocal
from app.domain.factor_analysis import FactorAnalysisRequest
from app.engine.canonical import canonical_json
from app.governance import GovernanceObjectStore
from app.services.analysis import (
    FactorAnalysisService,
    FactorObservationRepository,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate one factor over selected canonical LIVE_FORWARD outcomes."
    )
    parser.add_argument("--factor", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--horizon-days", type=int, default=5)
    parser.add_argument("--quantiles", type=int, default=5)
    parser.add_argument("--min-cross-section-size", type=int, default=20)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--store-root", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    request = FactorAnalysisRequest(
        factor_name=args.factor,
        start_date=args.start_date,
        end_date=args.end_date,
        horizon_days=args.horizon_days,
        quantiles=args.quantiles,
        min_cross_section_size=args.min_cross_section_size,
    )
    with SessionLocal() as session:
        observations = FactorObservationRepository(session).load(request)
        artifact = FactorAnalysisService(
            GovernanceObjectStore(args.store_root)
        ).analyze(
            request,
            observations,
            actor=args.actor,
            code_revision=args.code_revision,
            recorded_at=datetime.now(timezone.utc),
        )
        session.rollback()

    output = {
        "calculation_fingerprint": (
            artifact.calculation_identity.calculation_fingerprint
        ),
        "evidence_uri": artifact.evidence_uri,
        "event_object_id": artifact.event_object_id,
        "object_id": artifact.object_id,
        "result": artifact.result.canonical_payload(),
        "reused": artifact.reused,
    }
    _write_atomic(args.output, canonical_json(output) + "\n")
    print(
        json.dumps(
            {
                "evidence_status": artifact.result.diagnostics.evidence_status,
                "object_id": artifact.object_id,
                "observation_count": artifact.result.diagnostics.observation_count,
                "output": str(args.output),
                "reused": artifact.reused,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
