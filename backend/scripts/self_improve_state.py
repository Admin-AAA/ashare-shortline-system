"""Locked, atomic persistence for the autonomous self-improve loop.

The state JSON and append-only run log are shared by every worktree.  Writers
must use this module so a concurrent or interrupted run cannot leave partial
JSON or interleaved JSONL records behind.

Multi-researcher concurrency: a read-only operation grabs LOCK_SH (shared)
so multiple researchers can inspect state in parallel.  All writes still
serialise behind LOCK_EX.  The claim-direction / publish-champion /
publish-finding / release-direction / heartbeat subcommands provide the
concurrency-safe lifecycle for multi-worker self-improve.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

DEFAULT_MEMORY_DIR = Path(
    "/Users/wangao/.claude/projects/-Users-wangao/memory"
)

HEARTBEAT_TIMEOUT_SECONDS = 30 * 60  # 30 min


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _memory_dir() -> Path:
    return Path(os.environ.get("SELF_IMPROVE_MEMORY_DIR", DEFAULT_MEMORY_DIR))


@contextmanager
def _locked_shared(memory_dir: Path) -> Iterator[None]:
    """Shared lock for concurrent reads -- LOCK_SH instead of LOCK_EX."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    lock_path = memory_dir / "self-improve-state.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _locked(memory_dir: Path) -> Iterator[None]:
    """Exclusive lock for writes."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    lock_path = memory_dir / "self-improve-state.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            json.dump(value, temp_file, ensure_ascii=False, indent=2, sort_keys=True)
            temp_file.write("\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def read_state(memory_dir: Path | None = None) -> dict:
    """Read state with a shared lock so concurrent researchers don't block."""
    directory = memory_dir or _memory_dir()
    with _locked_shared(directory):
        return _read_json(directory / "self-improve-state.json")


def commit_update(
    state_patch: dict,
    run_record: dict | None = None,
    memory_dir: Path | None = None,
) -> dict:
    """Merge a top-level state patch and optionally append one run atomically.

    In multi-worker mode the caller should prefer publish-champion or
    publish-finding rather than directly mutating canonical_production.
    """
    if not isinstance(state_patch, dict):
        raise TypeError("state_patch must be a JSON object")
    if run_record is not None and not isinstance(run_record, dict):
        raise TypeError("run_record must be a JSON object")

    directory = memory_dir or _memory_dir()
    state_path = directory / "self-improve-state.json"
    runs_path = directory / "self-improve-runs.jsonl"
    with _locked(directory):
        state = _read_json(state_path)
        state.update(state_patch)
        _atomic_write_json(state_path, state)
        if run_record is not None:
            encoded = json.dumps(run_record, ensure_ascii=False, separators=(",", ":"))
            with runs_path.open("a", encoding="utf-8") as runs_file:
                runs_file.write(encoded + "\n")
                runs_file.flush()
                os.fsync(runs_file.fileno())
        return state


# -- multi-researcher lifecycle ------------------------------------------


def _ensure_registry(state: dict) -> dict:
    return state.setdefault("researcher_registry", {})


def _ensure_active(registry: dict) -> dict:
    return registry.setdefault("active_researchers", {})


def _purge_stale_workers(active: dict) -> dict[str, str]:
    """Remove stale workers and return their researcher-to-direction mapping."""
    now_dt = datetime.now(timezone.utc)
    stale: dict[str, str] = {}
    for rid, info in list(active.items()):
        hb = info.get("last_heartbeat", "")
        try:
            hb_dt = datetime.fromisoformat(hb)
            is_stale = (now_dt - hb_dt).total_seconds() > HEARTBEAT_TIMEOUT_SECONDS
        except (ValueError, TypeError):
            is_stale = True
        if is_stale:
            stale[rid] = str(info.get("claimed_direction", ""))
    for rid in stale:
        del active[rid]
    return stale


def _allocate_direction(
    state: dict, preferred: str | None, excluded: set[str]
) -> str | None:
    """Pick the highest-priority available direction, skipping excluded.

    Priority matches self-improve prompt section 3.2:
      1. cross_window_validation
      2. strategy_repair / loss_cluster
      3. exit_position_opt (grid scans)
      4. new_strategy / OOS validation
      5. engineering
      6. frontend
    """
    champion = state.get("canonical_production", {})
    champion_n = champion.get("metrics", {}).get("n", 0)
    champion_provisional = champion.get("provisional", True)
    archived = set(state.get("archived_falsified_directions", []))
    scanned = state.get("scanned_grids", {})

    candidates: list[tuple[int, str, str]] = []

    # Priority 1: champion stability verification
    if champion_provisional and champion_n < 50:
        d = "cross_window_validation:verify_champion_stability"
        if d not in excluded and d not in archived:
            candidates.append((1, d, "验证champion跨窗口稳定性"))

    # Priority 2: loss cluster attribution
    if champion_n < 50:
        d = "strategy_repair:loss_cluster_attribution"
        if d not in excluded and d not in archived:
            candidates.append((2, d, "亏损簇归因分析"))

    # Priority 3: unscanned parameter grids
    exhausted = set(scanned.keys())
    unscanned = _list_unscanned_grid_params(exhausted)
    for param_name in unscanned:
        d = f"grid_scan:{param_name}"
        if d not in excluded and d not in archived:
            candidates.append((3, d, f"参数网格: {param_name}"))

    # Priority 4a: independent OOS windows
    oos_windows = _list_unchecked_oos_windows(scanned)
    for win in oos_windows[:4]:
        d = f"oos_validation:{win}"
        if d not in excluded and d not in archived:
            candidates.append((4, d, f"OOS窗口验证: {win}"))

    # Priority 4b: new factor hypotheses
    factors = _list_unscanned_factor_directions(archived, excluded)
    for factor in factors[:4]:
        d = f"new_strategy:{factor}"
        if d not in excluded:
            candidates.append((4, d, f"新因子研究: {factor}"))

    # Priority 5: engineering
    d = "engineering:infrastructure_health_check"
    if d not in excluded and d not in archived:
        candidates.append((5, d, "基础设施健康检查"))

    # Priority 6: frontend
    candidates.append((6, "frontend:ux_improvement", "前端体验优化"))

    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1]))
    return candidates[0][1]


def _list_unscanned_grid_params(exhausted: set[str]) -> list[str]:
    """Return param names not yet scanned for the current champion."""
    all_params = [
        "stop_loss", "take_profit", "trailing_activate",
        "trailing_callback", "trailing_min_lock", "horizon",
        "profit_floor_ratio", "strategy_filter", "position_sizing",
        "entry_gap_filter", "score_line_threshold",
    ]
    available = []
    for p in all_params:
        if f"grid_scan:{p}" not in exhausted:
            found = any(p in k for k in exhausted)
            if not found:
                available.append(p)
    return available


def _list_unchecked_oos_windows(scanned: dict) -> list[str]:
    """Return time windows not yet validated."""
    checked: set[str] = set()
    for v in scanned.values():
        if isinstance(v, dict):
            w = v.get("window", "")
            if "oos" in w.lower() or "202" in w:
                checked.add(w)
    return [w for w in _OOS_WINDOWS if w not in checked]


_OOS_WINDOWS = [
    "2025-H2", "2025-H1", "2024-H2", "2024-H1",
    "2023-H2", "2023-H1", "2022-H2", "2022-H1",
]


def _list_unscanned_factor_directions(
    archived: set[str], excluded: set[str]
) -> list[str]:
    """Return factor hypotheses not yet falsified or claimed."""
    falsified = {d for d in archived if "factor" in d or "因子" in d}
    candidates = [
        "chip_concentration", "capital_flow_momentum",
        "northbound_flow", "institutional_ownership",
        "short_squeeze_pattern", "volume_price_divergence",
        "gap_fill_pattern", "sector_rotation_momentum",
    ]
    return [
        c for c in candidates
        if c not in falsified and f"new_strategy:{c}" not in excluded
    ]


def claim_direction(
    researcher_id: str,
    preferred: str | None = None,
    max_workers: int | None = None,
    force_stale: bool = False,
    memory_dir: Path | None = None,
) -> dict:
    """Atomically claim a non-overlapping research direction."""
    directory = memory_dir or _memory_dir()
    state_path = directory / "self-improve-state.json"

    with _locked(directory):
        state = _read_json(state_path)
        registry = _ensure_registry(state)
        active = _ensure_active(registry)
        max_w = max_workers or registry.get("max_concurrent_workers", 1)

        _purge_stale_workers(active)

        if not force_stale and len(active) >= max_w:
            return {"error": "capacity_full",
                    "active_count": len(active), "max": max_w}

        if force_stale and len(active) >= max_w:
            oldest = min(
                active.values(),
                key=lambda r: r.get("last_heartbeat", ""),
            )
            oldest_id = oldest.get("researcher_id", "")
            if oldest_id in active:
                del active[oldest_id]

        excluded: set[str] = set(
            state.get("archived_falsified_directions", [])
        )
        excluded.update(
            r.get("claimed_direction", "") for r in active.values()
        )
        claimed_dirs = registry.setdefault("claimed_directions", [])
        for d in claimed_dirs:
            if isinstance(d, str):
                excluded.add(d)

        # A preferred direction is an explicit reservation after the worker
        # has inspected state; still reject archived/active duplicates.
        if preferred and preferred not in excluded and preferred not in set(state.get("archived_falsified_directions", [])):
            direction = preferred
        else:
            direction = _allocate_direction(state, preferred, excluded)
        if direction is None:
            return {"error": "all_exhausted",
                    "excluded_count": len(excluded)}

        champion_commit = (
            state.get("canonical_production", {}).get("commit", "")
        )

        active[researcher_id] = {
            "researcher_id": researcher_id,
            "claimed_direction": direction,
            "claimed_at": _now_iso(),
            "last_heartbeat": _now_iso(),
            "champion_commit_at_claim": champion_commit,
            "status": "working",
        }
        claimed_dirs.append(direction)

        _atomic_write_json(state_path, state)
        return {"ok": True, "direction": direction,
                "champion_commit": champion_commit}


def release_direction(
    researcher_id: str,
    memory_dir: Path | None = None,
) -> dict:
    """Release a claimed research direction."""
    directory = memory_dir or _memory_dir()
    state_path = directory / "self-improve-state.json"

    with _locked(directory):
        state = _read_json(state_path)
        active = (
            state.get("researcher_registry", {})
            .get("active_researchers", {})
        )
        info = active.pop(researcher_id, None)
        claimed = (
            state.get("researcher_registry", {})
            .get("claimed_directions", [])
        )
        direction_to_drop = (
            info.get("claimed_direction", "") if isinstance(info, dict) else ""
        )
        if direction_to_drop:
            state["researcher_registry"]["claimed_directions"] = [
                direction for direction in claimed
                if direction != direction_to_drop
            ]

        _atomic_write_json(state_path, state)
        return {"ok": True}


def heartbeat(
    researcher_id: str,
    memory_dir: Path | None = None,
) -> dict:
    """Update heartbeat and check for champion staleness."""
    directory = memory_dir or _memory_dir()
    state_path = directory / "self-improve-state.json"

    with _locked(directory):
        state = _read_json(state_path)
        active = (
            state.get("researcher_registry", {})
            .get("active_researchers", {})
        )
        info = active.get(researcher_id)
        if info is None:
            return {"ok": True, "stale_champion": False,
                    "status": "not_registered"}
        info["last_heartbeat"] = _now_iso()
        status = info.get("status", "working")
        _atomic_write_json(state_path, state)
        return {"ok": True, "stale_champion": status == "invalidated",
                "status": status}


def publish_champion(
    researcher_id: str,
    expected_champion_commit: str,
    state_patch: dict,
    run_record: dict | None = None,
    memory_dir: Path | None = None,
) -> dict:
    """CAS-guarded champion publish. Returns error on commit mismatch."""
    if not isinstance(state_patch, dict):
        raise TypeError("state_patch must be a JSON object")

    directory = memory_dir or _memory_dir()
    state_path = directory / "self-improve-state.json"
    runs_path = directory / "self-improve-runs.jsonl"

    with _locked(directory):
        state = _read_json(state_path)
        current = state.get("canonical_production", {}).get("commit", "")
        if current != expected_champion_commit:
            return {
                "error": "CHAMPION_STALE",
                "expected": expected_champion_commit,
                "actual": current,
                "new_champion": state.get("canonical_production"),
            }

        state.update(state_patch)

        active = (
            state.get("researcher_registry", {})
            .get("active_researchers", {})
        )
        for rid, info in active.items():
            if rid != researcher_id:
                info["status"] = "invalidated"
                info["invalidated_at"] = _now_iso()
                info["invalidated_by"] = researcher_id

        _atomic_write_json(state_path, state)
        if run_record is not None:
            encoded = json.dumps(
                run_record, ensure_ascii=False, separators=(",", ":")
            )
            with runs_path.open("a", encoding="utf-8") as runs_file:
                runs_file.write(encoded + "\n")
                runs_file.flush()
                os.fsync(runs_file.fileno())
        return {"ok": True}


def publish_finding(
    researcher_id: str,
    state_patch: dict,
    finding: dict,
    run_record: dict | None = None,
    memory_dir: Path | None = None,
) -> dict:
    """Append a finding and optional state updates. No champion mutation."""
    if not isinstance(state_patch, dict):
        raise TypeError("state_patch must be a JSON object")
    if not isinstance(finding, dict):
        raise TypeError("finding must be a JSON object")
    if "canonical_production" in state_patch:
        raise ValueError(
            "publish_finding must not mutate canonical_production"
        )

    directory = memory_dir or _memory_dir()
    state_path = directory / "self-improve-state.json"
    runs_path = directory / "self-improve-runs.jsonl"

    with _locked(directory):
        state = _read_json(state_path)
        state.update(state_patch)
        findings: list = state.setdefault("published_findings", [])
        findings.append(finding)
        _atomic_write_json(state_path, state)
        if run_record is not None:
            encoded = json.dumps(
                run_record, ensure_ascii=False, separators=(",", ":")
            )
            with runs_path.open("a", encoding="utf-8") as runs_file:
                runs_file.write(encoded + "\n")
                runs_file.flush()
                os.fsync(runs_file.fileno())
        return {"ok": True}


# -- CLI -----------------------------------------------------------------


def _json_object(raw: str) -> dict:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("value must be a JSON object")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("read")

    commit_parser = subparsers.add_parser("commit")
    commit_parser.add_argument(
        "--state-patch", required=True, type=_json_object
    )
    commit_parser.add_argument("--run-record", type=_json_object)

    claim_parser = subparsers.add_parser("claim-direction")
    claim_parser.add_argument("--researcher-id", required=True)
    claim_parser.add_argument("--preferred-direction", default=None)
    claim_parser.add_argument("--max-workers", type=int, default=None)
    claim_parser.add_argument("--force-stale", action="store_true")

    subparsers.add_parser("release-direction").add_argument(
        "--researcher-id", required=True
    )

    subparsers.add_parser("heartbeat").add_argument(
        "--researcher-id", required=True
    )

    pub_champ = subparsers.add_parser("publish-champion")
    pub_champ.add_argument("--researcher-id", required=True)
    pub_champ.add_argument(
        "--expected-champion-commit", required=True
    )
    pub_champ.add_argument(
        "--state-patch", required=True, type=_json_object
    )
    pub_champ.add_argument("--run-record", type=_json_object)

    pub_find = subparsers.add_parser("publish-finding")
    pub_find.add_argument("--researcher-id", required=True)
    pub_find.add_argument(
        "--state-patch", required=True, type=_json_object
    )
    pub_find.add_argument(
        "--finding", required=True, type=_json_object
    )
    pub_find.add_argument("--run-record", type=_json_object)

    args = parser.parse_args()

    if args.command == "read":
        result = read_state()
    elif args.command == "commit":
        result = commit_update(args.state_patch, args.run_record)
    elif args.command == "claim-direction":
        result = claim_direction(
            args.researcher_id, args.preferred_direction,
            args.max_workers, args.force_stale,
        )
    elif args.command == "release-direction":
        result = release_direction(args.researcher_id)
    elif args.command == "heartbeat":
        result = heartbeat(args.researcher_id)
    elif args.command == "publish-champion":
        result = publish_champion(
            args.researcher_id, args.expected_champion_commit,
            args.state_patch, args.run_record,
        )
    elif args.command == "publish-finding":
        result = publish_finding(
            args.researcher_id, args.state_patch,
            args.finding, args.run_record,
        )
    else:
        result = {"error": f"unknown command {args.command}"}

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
