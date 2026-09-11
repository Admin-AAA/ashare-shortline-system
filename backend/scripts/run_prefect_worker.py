"""Synchronize deployments and start the local process worker."""
from __future__ import annotations

import subprocess
import sys

from app.core.deployment_identity import resolve_application_revision
from app.orchestration import schedule_all_enabled_tasks


def _pin_application_revision() -> None:
    """Pin worker provenance to the code loaded at startup.

    Uses the same env -> cached git HEAD -> "unknown" resolver as the API so
    the worker-side execution_version identity can never diverge from the
    enqueue side purely because of launch-path differences. Resolving here also
    mirrors the result into ``os.environ`` for the spawned worker subprocess.
    """
    resolve_application_revision()


def main() -> None:
    _pin_application_revision()
    subprocess.run(
        [sys.executable, "-m", "prefect", "work-pool", "create", "--type", "process", "ashare-local"],
        check=False,
    )
    schedule_all_enabled_tasks()
    raise SystemExit(
        subprocess.call([sys.executable, "-m", "prefect", "worker", "start", "--pool", "ashare-local"])
    )


if __name__ == "__main__":
    main()
