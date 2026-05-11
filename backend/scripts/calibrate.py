"""
Calibrate recommendation aggregation weights and DSA sub-score weights
from historical labeled outcomes.

Usage:
    python scripts/calibrate.py                 # Show default vs calibrated comparison
    python scripts/calibrate.py --apply         # Also write results to calibration_config table
    python scripts/calibrate.py --steps 8       # Use finer grid search (8 steps, fallback only)
    python scripts/calibrate.py --walk-forward 3  # Run walk-forward calibration with 3 windows
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.tables import CalibrationConfig
from app.services.calibrator import (
    AGG_WEIGHT_KEYS,
    DEFAULT_AGG_WEIGHTS,
    DEFAULT_DSA_WEIGHTS,
    DSA_WEIGHT_KEYS,
    calibrate_aggregation_weights,
    calibrate_dsa_weights,
    walk_forward_calibrate,
)


def print_comparison(title: str, default: dict[str, float], calibrated: dict[str, float]) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")
    print(f"  {'Key':<22} {'Default':>8} {'Calibrated':>10} {'Delta':>8}")
    print(f"  {'-' * 48}")
    for key in default:
        def_val = default.get(key, 0)
        cal_val = calibrated.get(key, def_val)
        delta = cal_val - def_val
        sign = "+" if delta >= 0 else ""
        print(f"  {key:<22} {def_val:>8.4f} {cal_val:>10.4f} {sign}{delta:>7.4f}")


def apply_to_db(db, agg_weights: dict[str, float], dsa_weights: dict[str, float]) -> None:
    """Write calibration results to CalibrationConfig table."""
    now = datetime.utcnow()
    all_weights = {**agg_weights, **dsa_weights}
    for key, value in all_weights.items():
        row = db.get(CalibrationConfig, key)
        if row is None:
            row = CalibrationConfig(key=key)
            db.add(row)
        row.value = float(value)
        row.updated_at = now
    db.commit()
    print(f"\n  Applied {len(all_weights)} weight entries to calibration_config table.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate recommendation weights from labeled outcomes")
    parser.add_argument("--apply", action="store_true", help="Write calibration results to DB")
    parser.add_argument("--steps", type=int, default=5, help="Grid search steps (default: 5, only used when sample_count < 50)")
    parser.add_argument("--walk-forward", type=int, default=0, metavar="N", help="Run walk-forward calibration with N windows (e.g. --walk-forward 3)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        print("\n  Running aggregation weight calibration...")
        agg_result = calibrate_aggregation_weights(db, steps=args.steps)
        print(f"  Total samples: {agg_result['total_sample_count']}")
        print(f"  Train/Test: {agg_result['train_sample_count']}/{agg_result['test_sample_count']}")
        print(f"  Search method: {agg_result.get('search_method', 'N/A')} ({agg_result.get('search_iterations', 0)} combos)")
        if "error" in agg_result:
            print(f"  WARNING: {agg_result['error']}")
        else:
            print(f"  Default      -> train composite: {agg_result.get('default_train_composite_score', 'N/A')}, test composite: {agg_result.get('default_test_composite_score', 'N/A')}, test AUC: {agg_result.get('default_test_roc_auc', 'N/A')}")
            print(f"  Calibrated   -> train composite: {agg_result['train_composite_score']}, test composite: {agg_result['test_composite_score']}, test AUC: {agg_result['test_roc_auc']}")

        print_comparison(
            "Aggregation Weights",
            agg_result.get("default_weights", DEFAULT_AGG_WEIGHTS),
            agg_result.get("optimal_weights", DEFAULT_AGG_WEIGHTS),
        )

        print("\n  Running DSA weight calibration...")
        dsa_result = calibrate_dsa_weights(db)
        print(f"  Samples: {dsa_result['sample_count']}")
        if "error" in dsa_result:
            print(f"  WARNING: {dsa_result['error']}")
        else:
            correlations = dsa_result.get("correlations", {})
            print(f"  DSA correlations: {correlations}")

        print_comparison(
            "DSA Sub-Score Weights",
            dsa_result.get("default_weights", DEFAULT_DSA_WEIGHTS),
            dsa_result.get("optimal_weights", DEFAULT_DSA_WEIGHTS),
        )

        if args.walk_forward > 0:
            print(f"\n  Running walk-forward calibration ({args.walk_forward} windows)...")
            wf_result = walk_forward_calibrate(db, n_windows=args.walk_forward)
            if "error" in wf_result:
                print(f"  WARNING: {wf_result['error']}")
            else:
                print(f"  Avg test composite score: {wf_result['avg_test_composite_score']}")
                print(f"  Avg test ROC AUC: {wf_result['avg_test_roc_auc']}")
                for w in wf_result["windows"]:
                    print(f"    Window {w['window']}: train={w['train_sample_count']}, test={w['test_sample_count']}, "
                          f"composite={w['test_composite_score']}, AUC={w['test_roc_auc']}")

        if args.apply:
            if "error" not in agg_result and "error" not in dsa_result:
                apply_to_db(
                    db,
                    agg_result["optimal_weights"],
                    dsa_result["optimal_weights"],
                )
            else:
                print("\n  Skipping --apply due to calibration warnings.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
