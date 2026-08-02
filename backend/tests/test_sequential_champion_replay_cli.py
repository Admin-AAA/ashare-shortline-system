from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.si_sequential_champion_replay import baseline_artifact_status


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend" / "scripts" / "si_sequential_champion_replay.py"
OUTPUT = ROOT / "output" / "sequential_champion_replay.json"


def test_help_has_no_database_or_output_side_effect() -> None:
    before = OUTPUT.stat().st_mtime_ns if OUTPUT.exists() else None
    result = subprocess.run(
        [str(ROOT / "backend" / ".venv" / "bin" / "python"), str(SCRIPT), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    after = OUTPUT.stat().st_mtime_ns if OUTPUT.exists() else None
    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()
    assert after == before


def test_empty_production_champion_is_invalid_baseline() -> None:
    assert baseline_artifact_status({"n": 0}) == "INVALID_BASELINE"
    assert baseline_artifact_status({"n": 1}) == "COMPLETED"
