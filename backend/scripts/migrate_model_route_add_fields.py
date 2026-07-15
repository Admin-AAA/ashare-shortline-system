#!/usr/bin/env python3
"""Compatibility entry point for the versioned model-route configuration migration.

The historical script issued three unmanaged ``ALTER TABLE`` statements.  It now
uses the same lock, preflight, shape verification and ledger as application
startup, so invoking it remains safe for operators without creating a second DDL
path.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.migrations import run_migrations
from app.db.session import engine


def migrate() -> None:
    result = run_migrations(engine)
    managed_versions = {
        "V003_add_model_route_max_tokens",
        "V004_add_model_route_temperature",
        "V005_add_model_route_timeout_seconds",
    }
    applied = [version for version in result.applied_versions if version in managed_versions]
    skipped = [version for version in result.skipped_versions if version in managed_versions]
    print("model_route_config migration completed through the versioned runner")
    print(f"applied={','.join(applied) or '-'}")
    print(f"already_current={','.join(skipped) or '-'}")


if __name__ == "__main__":
    migrate()
