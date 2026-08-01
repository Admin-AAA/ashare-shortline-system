#!/usr/bin/env python3
"""Run one formal Champion/Challenger experiment and emit canonical evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any
from uuid import uuid4


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_ROOT / ".env")

from app.db.session import SessionLocal  # noqa: E402
from app.engine.backtest import BacktestRequest, DateRange, ExecutionPolicy  # noqa: E402
from app.services.backtest.canonical_experiment import (  # noqa: E402
    CanonicalBacktestExperiment,
    CanonicalExperimentSpec,
)
from app.services.backtest.research_repository import ResearchBacktestRepository  # noqa: E402
from app.services.backtest.research_runner import ResearchBacktestRunner  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a same-snapshot canonical Champion/Challenger backtest experiment."
    )
    parser.add_argument("--start", required=True, help="First signal date (YYYY-MM-DD).")
    parser.add_argument("--end", required=True, help="Last signal date (YYYY-MM-DD).")
    parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--champion-params", required=True, help="Champion parameter_set JSON object.")
    parser.add_argument("--challenger-params", required=True, help="Challenger parameter_set JSON object.")
    parser.add_argument(
        "--execution-policy",
        default="{}",
        help="ExecutionPolicy JSON overrides; omitted fields use domain defaults.",
    )
    parser.add_argument("--hypothesis", required=True)
    parser.add_argument("--experiment-id")
    parser.add_argument("--target-code", action="append", default=[])
    return parser


def _json_object(raw: str, field: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    return value


def build_spec(args: argparse.Namespace) -> CanonicalExperimentSpec:
    selector = DateRange(str(args.start), str(args.end))
    execution_policy = ExecutionPolicy(
        **_json_object(str(args.execution_policy), "execution_policy")
    )
    common = {
        "strategy_id": str(args.strategy_id),
        "date_selector": selector,
        "execution_policy": execution_policy,
        "target_codes": tuple(args.target_code),
    }
    return CanonicalExperimentSpec(
        experiment_id=str(args.experiment_id or f"btexp_{uuid4().hex}"),
        hypothesis=str(args.hypothesis),
        champion=BacktestRequest(
            **common,
            parameter_set=_json_object(str(args.champion_params), "champion_params"),
        ),
        challenger=BacktestRequest(
            **common,
            parameter_set=_json_object(str(args.challenger_params), "challenger_params"),
        ),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    session = SessionLocal()
    try:
        repository = ResearchBacktestRepository(session)
        artifact = CanonicalBacktestExperiment(
            repository,
            ResearchBacktestRunner(repository),
        ).run(build_spec(args))
        print(json.dumps(artifact, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 2 if artifact["status"] == "failed" else 0
    except Exception as exc:  # CLI boundary: stable JSON on stderr for automation.
        session.rollback()
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": {
                        "code": str(getattr(exc, "code", "canonical_experiment_failed")),
                        "message": str(exc),
                    },
                },
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
