from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal  # noqa: E402
from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY  # noqa: E402
from scripts.research_entry_mode_three_window import (  # noqa: E402
    WINDOWS,
    _load_bars,
    _load_panel,
    _selected_rows,
    _simulate_row,
)
from scripts.research_wr70_entry_guard import CURRENT_NEXT_OPEN_STRATEGIES  # noqa: E402
from scripts.research_wr70_exit_screen import _simulate_records  # noqa: E402


def _key(row: dict[str, Any]) -> str:
    return f"{row['signal_date']}:{row['code']}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    panels = {
        window: _load_panel(args.panel_dir / f"entry_panel_{window}.json")
        for window in WINDOWS
    }
    rows_by_window = {window: _selected_rows(panel) for window, panel in panels.items()}
    all_rows = [row for rows in rows_by_window.values() for row in rows]
    db = SessionLocal()
    try:
        bars_by_code = _load_bars(db, all_rows)
        outcome_records = {
            window: [
                _simulate_row(
                    row,
                    bars_by_code.get(row["code"], []),
                    challenger=True,
                    challenger_best_strategies=CURRENT_NEXT_OPEN_STRATEGIES,
                )
                for row in rows
            ]
            for window, rows in rows_by_window.items()
        }
    finally:
        db.close()
    pure_records = {
        window: _simulate_records(
            rows,
            bars_by_code,
            DEFAULT_EXECUTION_POLICY,
        )
        for window, rows in rows_by_window.items()
    }

    windows: dict[str, Any] = {}
    for window in WINDOWS:
        outcome = {_key(row): row for row in outcome_records[window]}
        pure = {_key(row): row for row in pure_records[window]}
        differences: list[dict[str, Any]] = []
        for record_key in sorted(set(outcome) | set(pure)):
            left = outcome[record_key]
            right = pure[record_key]
            net_delta = None
            if left.get("net_return_pct") is not None and right.get("net_return_pct") is not None:
                net_delta = float(right["net_return_pct"]) - float(left["net_return_pct"])
            if (
                bool(left.get("settled")) == bool(right.get("settled"))
                and bool(left.get("executed")) == bool(right.get("executed"))
                and (net_delta is None or abs(net_delta) <= 0.01)
            ):
                continue
            differences.append(
                {
                    "key": record_key,
                    "outcome_service": {
                        "settled": left.get("settled"),
                        "executed": left.get("executed"),
                        "net_return_pct": left.get("net_return_pct"),
                        "exit_reason": left.get("exit_reason"),
                    },
                    "pure_execution": {
                        "settled": right.get("settled"),
                        "executed": right.get("executed"),
                        "net_return_pct": right.get("net_return_pct"),
                        "exit_reason": right.get("exit_reason"),
                    },
                    "net_delta_pct": net_delta,
                }
            )
        windows[window] = {
            "selected": len(rows_by_window[window]),
            "outcome_settled": sum(bool(row.get("settled")) for row in outcome.values()),
            "pure_settled": sum(bool(row.get("settled")) for row in pure.values()),
            "difference_count": len(differences),
            "differences": differences,
        }

    output = {
        "method": "same selected cards and bars; production outcome service versus canonical pure execution",
        "windows": windows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(json.dumps({
        window: {
            key: value
            for key, value in payload.items()
            if key != "differences"
        }
        for window, payload in windows.items()
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
