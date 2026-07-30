"""Synchronize deployments and start the local process worker."""
from __future__ import annotations

import subprocess
import sys

from app.orchestration import schedule_all_enabled_tasks


def main() -> None:
    schedule_all_enabled_tasks()
    raise SystemExit(
        subprocess.call([sys.executable, "-m", "prefect", "worker", "start", "--pool", "ashare-local"])
    )


if __name__ == "__main__":
    main()
