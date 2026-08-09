#!/usr/bin/env python3
"""Run the pre-registered small-cap quality policy on its historical holdout."""
from __future__ import annotations

import json
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_ROOT / ".env")

from app.db.session import SessionLocal  # noqa: E402
from app.engine.backtest import canonical_fingerprint  # noqa: E402
from app.services.backtest.quality_validation import (  # noqa: E402
    FrozenQualityValidationSpec,
)
from app.services.backtest.strategy_profile import (  # noqa: E402
    StrategyBacktestProfiles,
)
from app.services.backtest.research_runner import (  # noqa: E402
    RESEARCH_SOURCE_VERSION,
    RESEARCH_STOCK_METADATA_TEMPORALITY,
)
STRATEGY_ID = "small-cap-thematic-momentum"
AS_OF = "2026-08-07"
SPEC = FrozenQualityValidationSpec(
    strategy_id=STRATEGY_ID,
    strategy_version="39457718f5b53fcfb49d88ffc10ef2cc6adeeab4dea31bfe1f29bf84c5564d5d",
    holdout_date_from="2025-01-15",
    holdout_date_to="2026-01-16",
    discovery_date_from="2026-01-31",
    expected_signal_days=244,
    signal_dates_fingerprint="3037c3c58fc4b8b74f4152509b3c03c50b94d7c344b2aa563bcec585f5d80e9d",
    quality_fingerprint="545cae589c221b63c0f493f3ba974be22f537fb286af210ff54f798210353a6b",
    execution_policy_fingerprint="b79ec9f6e5a4b99a69268d7362865df076d0612508a09abc01b52151d9982810",
    parameter_fingerprint="6ed85ea9c73d6809c64d0fc57d69e367fb8e1c715e1e5d2aee522a9126a0ab1a",
)


def main() -> int:
    session = SessionLocal()
    try:
        profiles = StrategyBacktestProfiles(session)
        current = profiles.current_target(STRATEGY_ID, as_of=AS_OF)
        if current is None:
            raise RuntimeError("small-cap strategy target is unavailable")
        if current.strategy_version != SPEC.strategy_version:
            raise RuntimeError("strategy version differs from frozen holdout spec")
        if current.execution_policy.fingerprint() != SPEC.execution_policy_fingerprint:
            raise RuntimeError("execution policy differs from frozen holdout spec")
        if canonical_fingerprint(dict(current.parameter_set)) != SPEC.parameter_fingerprint:
            raise RuntimeError("strategy parameters differ from frozen holdout spec")
        metadata_ready = RESEARCH_STOCK_METADATA_TEMPORALITY == "point_in_time"
        blocker = (
            "preclaimed_snapshot_and_two_phase_reveal_required"
            if metadata_ready
            else "point_in_time_stock_metadata_required"
        )
        print(
            json.dumps(
                {
                    "status": "INVALID_EVIDENCE",
                    "historically_qualified": False,
                    "production_release_ready": False,
                    "production_blocker": blocker,
                    "source_version": RESEARCH_SOURCE_VERSION,
                    "stock_metadata_temporality": RESEARCH_STOCK_METADATA_TEMPORALITY,
                    "spec_fingerprint": SPEC.fingerprint,
                    "holdout_consumed": False,
                    "error": "formal evaluation requires a real preclaimed snapshot before outcomes are revealed",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    except Exception as exc:
        session.rollback()
        print(
            json.dumps(
                {"status": "INVALID_EVIDENCE", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
