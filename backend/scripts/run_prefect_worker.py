"""Synchronize deployments and start the local process worker."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.orchestration import schedule_all_enabled_tasks


def _pin_application_revision() -> None:
    """Keep worker provenance aligned with the code it loaded at startup."""
    if str(os.environ.get("ASHARE_APPLICATION_REVISION") or "").strip():
        return
    repository_root = Path(__file__).resolve().parents[2]
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return
    if len(revision) >= 7:
        os.environ["ASHARE_APPLICATION_REVISION"] = revision


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
