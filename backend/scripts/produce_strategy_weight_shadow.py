"""Generate the read-only Bayesian strategy-weight shadow artifact."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal
from app.governance.store import GovernanceObjectStore
from app.services.analysis.shadow_weight import create_strategy_weight_shadow


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--governance-store", type=Path, default=None)
    parser.add_argument("--created-by", default="strategy-weight-shadow-cli")
    parser.add_argument("--code-revision", default=None)
    args = parser.parse_args()
    store = GovernanceObjectStore(args.governance_store) if args.governance_store else GovernanceObjectStore()
    db = SessionLocal()
    try:
        envelope = create_strategy_weight_shadow(
            db,
            store=store,
            created_by=args.created_by,
            code_revision=args.code_revision,
        )
    finally:
        db.close()
    print(
        json.dumps(
            {
                "status": "shadow_only",
                "object_id": envelope.object_id,
                "evidence_uri": store.evidence_uri_for(envelope.object_id),
                "active_configuration_modified": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
