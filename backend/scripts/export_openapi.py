"""Export the canonical FastAPI OpenAPI document for frontend code generation."""
from __future__ import annotations

import json
from pathlib import Path

from app.main import app


OUTPUT = Path("/tmp/ashare-openapi.json")


def main() -> None:
    OUTPUT.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
