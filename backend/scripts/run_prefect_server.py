"""Start the local Prefect server as an explicit development process."""
from __future__ import annotations

import subprocess
import sys


def main() -> None:
    raise SystemExit(subprocess.call([sys.executable, "-m", "prefect", "server", "start"]))


if __name__ == "__main__":
    main()
