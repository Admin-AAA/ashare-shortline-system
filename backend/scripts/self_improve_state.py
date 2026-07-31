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
import shutil
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

DEFAULT_MEMORY_DIR = Path(
    "/Users/wangao/.claude/projects/-Users-wangao/memory"
)

HEARTBEAT_TIMEOUT_SECONDS = 30 * 60  # 30 min
DEFAULT_DAEMON_INTERVAL_SECONDS = 900  # 15 min
DEFAULT_DAEMON_MAX_DURATION_SECONDS = 3 * 3600  # bound abandoned daemons

# Bump when the shape of self-improve-state.json changes meaningfully, so
# older tools/researchers can detect that they read a newer layout.
STATE_SCHEMA_VERSION = 2


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _memory_dir() -> Path:
    return Path(os.environ.get("SELF_IMPROVE_MEMORY_DIR", DEFAULT_MEMORY_DIR))


def _acquire_publish_lock(
    lock_path: Path, timeout_seconds: float = 180
) -> int:
    """Try to acquire the publish lock exclusively within the timeout.

    Returns the held fd (flock locks are bound to the open file description,
    so release = closing this fd).  Returns -1 on timeout.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        except OSError:
            return -1
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except (BlockingIOError, OSError):
            os.close(fd)
            if time.monotonic() >= deadline:
                return -1
            time.sleep(2)


def with_publish_lock(
    command: list[str],
    timeout_seconds: float = 180,
    repo: Path | None = None,
) -> dict:
    """Run a shell command under an exclusive cross-worktree lock.

    Serialises concurrent ``git merge --no-ff`` + push against main across
    multiple /loop windows.  Uses a lockfile at the repo root
    (``.git-self-improve-publish.lock``) so every worktree shares it.

    The lockfile is left in place (empty); the lock lives on the held fd and
    is released by closing it.  Removing the file while another process may
    be waiting would let two processes lock different inodes — do not unlink.
    """
    repo_root = Path(repo) if repo else _repo_root()
    lock_path = repo_root / ".git-self-improve-publish.lock"
    fd = _acquire_publish_lock(lock_path, timeout_seconds)
    if fd == -1:
        return {"error": "publish_lock_timeout", "lock_path": str(lock_path)}
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, cwd=str(repo_root)
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    finally:
        os.close(fd)


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
    value.setdefault("schema_version", STATE_SCHEMA_VERSION)
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


# State-patch merge semantics. Shallow `state.update(patch)` replaces whole
# sub-dictionaries, so a finding that patches `scanned_grids` with only its own
# keys silently drops every prior completed grid scan — which then lets the
# direction pool re-allocate an already-falsified grid. These accumulation
# fields are merged/union'd instead of replaced.
_MERGE_DICT_KEYS = ("scanned_grids",)
_UNION_LIST_KEYS = ("archived_falsified_directions", "published_findings")


def _apply_state_patch(state: dict, patch: dict) -> None:
    """Apply a state patch with merge semantics for accumulation fields.

    Everything else keeps replace semantics (canonical_production,
    researcher_registry, blocked_until, next_event, ...).
    """
    for key, value in patch.items():
        if key in _MERGE_DICT_KEYS and isinstance(value, dict):
            target = state.setdefault(key, {})
            if not isinstance(target, dict):
                target = {}
                state[key] = target
            target.update(value)
        elif key in _UNION_LIST_KEYS and isinstance(value, list):
            target = state.setdefault(key, [])
            if not isinstance(target, list):
                target = []
                state[key] = target
            for item in value:
                if item not in target:
                    target.append(item)
        else:
            state[key] = value


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
        _apply_state_patch(state, state_patch)
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
    """Remove stale workers and return their researcher-to-direction mapping.

    A stale worker's claimed direction is returned in the mapping so the
    caller can also release it from ``claimed_directions``.  Without this a
    direction claimed by a crashed/forgotten worker would stay permanently
    excluded from allocation and the direction pool would slowly leak until
    ``all_exhausted``.
    """
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


def _release_claimed_directions(
    state: dict, directions: list[str]
) -> None:
    """Drop released direction strings from claimed_directions (in place)."""
    claimed = (
        state.get("researcher_registry", {})
        .get("claimed_directions", [])
    )
    if not directions or not claimed:
        return
    stale_dirs = {d for d in directions if d}
    if not stale_dirs:
        return
    state["researcher_registry"]["claimed_directions"] = [
        d for d in claimed
        if not (isinstance(d, str) and d in stale_dirs)
    ]


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
        # archived 条目可能带后缀原因（如 oos_validation:2024-H1:数据不足），
        # 需前缀匹配：d 本身或 d + ":" 开头都视为已归档。
        archived_prefix = f"{d}:"
        if (
            d not in excluded
            and d not in archived
            and not any(a.startswith(archived_prefix) for a in archived)
        ):
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


# 参数名 → 扫描键主题词映射。历史 scanned_grids 记录键用主题词命名
# （score_line_on_2025_2026 而非 score_line_threshold_...），直接用参数名做
# 子串匹配会漏判已扫描网格，让方向池反复分配已 FALSIFIED 的方向。
# 主题词取自记录键的核心标识；匹配策略：任一主题词是扫描键子串即视为已扫。
_GRID_PARAM_TOPICS = {
    "stop_loss": ["stop_loss"],
    "take_profit": ["take_profit", "tp"],
    "trailing_activate": ["trailing_activate", "activate"],
    "trailing_callback": ["trailing_callback", "callback"],
    "trailing_min_lock": ["trailing_min_lock", "min_lock"],
    "horizon": ["horizon"],
    "profit_floor_ratio": ["profit_floor_ratio", "profit_floor"],
    "strategy_filter": ["strategy_filter", "strategy_filtering"],
    "position_sizing": ["position_sizing"],
    "entry_gap_filter": ["entry_gap_filter", "entry_gap"],
    "score_line_threshold": ["score_line_threshold", "score_line"],
}


def _list_unscanned_grid_params(exhausted: set[str]) -> list[str]:
    """Return param names not yet scanned for the current champion."""
    all_params = list(_GRID_PARAM_TOPICS.keys())
    available = []
    for p in all_params:
        if f"grid_scan:{p}" not in exhausted:
            topics = _GRID_PARAM_TOPICS[p]
            found = any(t in k for k in exhausted for t in topics)
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
    """Return factor hypotheses not yet falsified or claimed.

    A factor is falsified when any archived entry starts with
    ``new_strategy:{factor}:`` (archived 字符串可能带后缀原因说明)。旧实现用
    "factor"/"因子" 子串匹配会漏掉 new_strategy:capital_flow_momentum:... 这类
    已否证方向，让方向池反复分配。
    """
    candidates = [
        "chip_concentration", "capital_flow_momentum",
        "northbound_flow", "institutional_ownership",
        "short_squeeze_pattern", "volume_price_divergence",
        "gap_fill_pattern", "sector_rotation_momentum",
    ]
    falsified = {
        d.split(":", 2)[1]
        for d in archived
        if d.startswith("new_strategy:") and len(d.split(":", 2)) >= 2
    }
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

        _release_claimed_directions(
            state, list(_purge_stale_workers(active).values())
        )

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
                # The displaced worker's direction must be released too,
                # otherwise it stays permanently excluded from allocation.
                _release_claimed_directions(
                    state, [str(oldest.get("claimed_direction", ""))]
                )

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
        _terminate_researcher_daemon(info)
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


# -- worktree isolation ---------------------------------------------------
#
# /loop windows share one checkout; every `git switch -c` in one window swaps
# the files other windows are reading.  setup-worktree / release-worktree give
# each researcher its own detached worktree under <repo>/.worktrees/si-*, and
# register the path in the researcher record so release can clean it up.


def _repo_root() -> Path:
    """Absolute path of the main working tree (the checkout with .git)."""
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    git_dir = Path(common)
    if git_dir.name == ".git":
        return git_dir.parent
    # Linked worktree: <repo>/.git/worktrees/<name> -> common dir is the
    # main repo's .git, parent is still the repo root.
    return git_dir.parent


def setup_worktree(
    researcher_id: str,
    base_ref: str = "main",
    memory_dir: Path | None = None,
) -> dict:
    """Create a detached worktree for a researcher and register its path.

    The worktree is placed under the main repo's ``.worktrees/si-<suffix>``
    (gitignored).  The researcher record gains a ``worktree_path`` field so
    release-direction can remove it later.
    """
    repo = _repo_root()
    run_id = researcher_id.replace("/", "_")
    wt_dir = repo / ".worktrees" / f"si-{run_id}"
    wt_dir.parent.mkdir(parents=True, exist_ok=True)

    if wt_dir.exists() and (wt_dir / ".git").exists():
        # Already set up (e.g. resume) — just re-register.
        pass
    else:
        proc = subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "--detach",
             str(wt_dir), base_ref],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            return {
                "error": "worktree_add_failed",
                "stderr": proc.stderr.strip(),
            }

    directory = memory_dir or _memory_dir()
    state_path = directory / "self-improve-state.json"
    with _locked(directory):
        state = _read_json(state_path)
        info = (
            state.get("researcher_registry", {})
            .get("active_researchers", {})
            .get(researcher_id)
        )
        if not isinstance(info, dict):
            return {"error": "not_registered", "researcher_id": researcher_id}
        info["worktree_path"] = str(wt_dir)
        _atomic_write_json(state_path, state)

    return {"ok": True, "worktree_path": str(wt_dir)}


def release_worktree(
    researcher_id: str,
    remove: bool = False,
    memory_dir: Path | None = None,
) -> dict:
    """Remove the registered worktree for a researcher (optional).

    By default only clears the ``worktree_path`` from the researcher record
    and returns it (so a subsequent ``--remove`` still knows where to look).
    With ``--remove`` also deletes the worktree directory (the agent should
    already have committed / merged its branch — git refuses if it is dirty).
    """
    directory = memory_dir or _memory_dir()
    state_path = directory / "self-improve-state.json"

    with _locked(directory):
        state = _read_json(state_path)
        info = (
            state.get("researcher_registry", {})
            .get("active_researchers", {})
            .get(researcher_id)
        )
        path = info.get("worktree_path") if isinstance(info, dict) else None
        if isinstance(info, dict):
            info.pop("worktree_path", None)
            _atomic_write_json(state_path, state)

    if remove:
        # Fall back to the deterministic path even when the registry entry
        # was already cleared by an earlier (non-remove) release call.
        if not path:
            path = str(_repo_root() / ".worktrees" / f"si-{researcher_id.replace('/', '_')}")
        wt = Path(path)
        if (wt / ".git").exists():
            repo = _repo_root()
            proc = subprocess.run(
                ["git", "-C", str(repo), "worktree", "remove", str(wt)],
                capture_output=True, text=True,
            )
            if proc.returncode != 0:
                return {
                    "ok": True,
                    "removed": False,
                    "worktree_path": str(wt),
                    "stderr": proc.stderr.strip(),
                }
            shutil.rmtree(wt, ignore_errors=True)
        elif wt.exists():
            shutil.rmtree(wt, ignore_errors=True)
        return {"ok": True, "removed": True, "worktree_path": str(wt)}

    return {"ok": True, "removed": False, "worktree_path": path}


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


def prune_claimed_directions(memory_dir: Path | None = None) -> dict:
    """Release claimed directions whose worker is no longer active.

    Guards against the direction-pool leak when a worker is force-replaced
    or purged as stale without a release-direction (crashed terminal window,
    interrupted /loop session).  Every claim appends to claimed_directions;
    entries whose researcher is gone from active_researchers are reclaimed.
    """
    directory = memory_dir or _memory_dir()
    state_path = directory / "self-improve-state.json"

    with _locked(directory):
        state = _read_json(state_path)
        registry = state.setdefault("researcher_registry", {})
        active = registry.setdefault("active_researchers", {})
        claimed = registry.setdefault("claimed_directions", [])
        if not claimed:
            return {"ok": True, "released": []}

        active_dirs = {
            str(info.get("claimed_direction", ""))
            for info in active.values()
            if isinstance(info, dict)
        }
        released = [
            d for d in claimed
            if isinstance(d, str) and d not in active_dirs
        ]
        if released:
            registry["claimed_directions"] = [
                d for d in claimed
                if not (isinstance(d, str) and d in set(released))
            ]
            _atomic_write_json(state_path, state)
        return {"ok": True, "released": released}


def daemon_heartbeat(
    researcher_id: str,
    interval_seconds: int = DEFAULT_DAEMON_INTERVAL_SECONDS,
    max_duration_seconds: int = DEFAULT_DAEMON_MAX_DURATION_SECONDS,
    memory_dir: Path | None = None,
) -> int:
    """Run a foreground heartbeat daemon until stopped or abandoned.

    Repeatedly calls :func:`heartbeat` so a long-running researcher stays
    claim-eligible without relying on the agent remembering to heartbeat.
    The daemon registers its own PID in the researcher's record so that
    release-direction can terminate it immediately.

    Terminates when:

    - the researcher is no longer registered (released / purged), or
    - the researcher is invalidated (another worker published a champion), or
    - the session leader (the terminal running the /loop window) is gone, or
    - the daemon exceeds ``max_duration_seconds``.

    Exit codes: 0 = clean stop, 42 = champion invalidated.
    """
    interval = max(1, int(interval_seconds))
    max_duration = max(interval, int(max_duration_seconds))
    memory_dir = Path(memory_dir) if memory_dir else None
    _register_daemon_pid(researcher_id, memory_dir)
    # Default SIGTERM kills the process outright; translate it into a clean
    # stop so release-direction can shut the daemon down with exit code 0.
    def _handle_term(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handle_term)
    start = time.monotonic()
    try:
        while True:
            if (time.monotonic() - start) > max_duration:
                return 0
            result = heartbeat(researcher_id, memory_dir)
            status = result.get("status")
            if status in ("not_registered", None):
                return 0
            if status == "invalidated":
                return 42
            if not _session_leader_alive():
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        # SIGINT / SIGTERM raised as KeyboardInterrupt by the default
        # handler — treat as a clean stop, not a failure.
        return 0


def _register_daemon_pid(
    researcher_id: str, memory_dir: Path | None = None
) -> None:
    """Record the daemon's PID in the researcher's active record."""
    directory = Path(memory_dir) if memory_dir else _memory_dir()
    state_path = directory / "self-improve-state.json"
    with _locked(directory):
        state = _read_json(state_path)
        info = (
            state.get("researcher_registry", {})
            .get("active_researchers", {})
            .get(researcher_id)
        )
        if isinstance(info, dict):
            info["heartbeat_daemon_pid"] = os.getpid()
            _atomic_write_json(state_path, state)


def _terminate_researcher_daemon(info) -> None:
    """Best-effort SIGTERM to the heartbeat daemon of a researcher record."""
    pid = info.get("heartbeat_daemon_pid") if isinstance(info, dict) else None
    if isinstance(pid, int) and pid > 0:
        stop_daemon_process(pid)


def _session_leader_alive() -> bool:
    """True while the daemon's controlling-session leader is still alive.

    A /loop window runs inside a terminal session; when that window is
    closed its session leader dies even though this daemon (spawned with
    ``&``) gets reparented to launchd.  Detecting the session leader avoids
    daemons lingering after their window is gone.
    """
    try:
        sid = os.getsid(0)
    except OSError:
        return True  # no session info (headless) — do not self-terminate
    if sid <= 1:
        return False
    try:
        os.kill(sid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def stop_daemon_process(pid: int) -> bool:
    """Terminate a heartbeat daemon (registered under a researcher id)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _encode_provisional_guard(
    state_patch: dict,
    current_champion: dict,
) -> tuple[str, str] | None:
    """Encode L-019: small-sample champions must stay marked provisional.

    Returns (key, message) when a publish would clear the provisional flag on
    a champion whose n < PROVISIONAL_MIN_N.  The caller refuses the publish so
    a tiny champion can never be declared stable/optimal by clearing the flag.
    """
    PROVISIONAL_MIN_N = 50
    new_cp = state_patch.get("canonical_production")
    if not isinstance(new_cp, dict):
        return None
    n = new_cp.get("metrics", {}).get("n") if isinstance(new_cp.get("metrics"), dict) else None
    was_provisional = current_champion.get("provisional", True)
    if (
        isinstance(n, int)
        and n < PROVISIONAL_MIN_N
        and new_cp.get("provisional") is False
        and was_provisional
    ):
        return (
            "SMALL_SAMPLE_PROVISIONAL_GUARD",
            f"champion n={n} < {PROVISIONAL_MIN_N}，禁止清除 provisional 标志（L-019）",
        )
    return None


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

        guard = _encode_provisional_guard(
            state_patch, state.get("canonical_production", {})
        )
        if guard is not None:
            key, message = guard
            return {"error": key, "message": message}

        _apply_state_patch(state, state_patch)

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


def _finding_has_substance(finding: dict) -> str | None:
    """Return a reason string when a finding is an empty round, else None.

    Encodes the "每轮必须交付研究产出" rule: a finding without a summary,
    without metrics, and without a decision is just a heartbeat — it must not
    be recorded as a finding (which would let an idle round masquerade as
    work).
    """
    if not isinstance(finding.get("summary"), str) or not finding["summary"].strip():
        return "finding 缺 summary（空轮次）"
    metrics = finding.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        return "finding 缺 metrics（空轮次）"
    decision = finding.get("decision")
    if not decision or not str(decision).strip():
        return "finding 缺 decision（空轮次）"
    return None


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

    empty_reason = _finding_has_substance(finding)
    if empty_reason is not None:
        return {"error": "EMPTY_FINDING", "message": empty_reason}

    directory = memory_dir or _memory_dir()
    state_path = directory / "self-improve-state.json"
    runs_path = directory / "self-improve-runs.jsonl"

    with _locked(directory):
        state = _read_json(state_path)
        _apply_state_patch(state, state_patch)
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

    setup_wt = subparsers.add_parser("setup-worktree")
    setup_wt.add_argument("--researcher-id", required=True)
    setup_wt.add_argument("--base-ref", default="main")

    release_wt = subparsers.add_parser("release-worktree")
    release_wt.add_argument("--researcher-id", required=True)
    release_wt.add_argument("--remove", action="store_true")

    pub_lock = subparsers.add_parser("with-publish-lock")
    pub_lock.add_argument(
        "--cmd", required=True,
        help="shell command to run under the lock; pass as ONE quoted argument",
    )
    pub_lock.add_argument("--timeout", type=int, default=180)

    subparsers.add_parser("prune-claimed")

    daemon_parser = subparsers.add_parser("daemon-heartbeat")
    daemon_parser.add_argument("--researcher-id", required=True)
    daemon_parser.add_argument("--interval", type=int, default=None)
    daemon_parser.add_argument("--max-duration", type=int, default=None)

    stop_parser = subparsers.add_parser("stop-daemon")
    stop_parser.add_argument("--pid", type=int, required=True)

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
    elif args.command == "setup-worktree":
        result = setup_worktree(args.researcher_id, args.base_ref)
    elif args.command == "release-worktree":
        result = release_worktree(args.researcher_id, args.remove)
    elif args.command == "with-publish-lock":
        result = with_publish_lock(
            ["/bin/sh", "-c", args.cmd], timeout_seconds=args.timeout
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return
    elif args.command == "prune-claimed":
        result = prune_claimed_directions()
    elif args.command == "daemon-heartbeat":
        result = daemon_heartbeat(
            args.researcher_id,
            args.interval or DEFAULT_DAEMON_INTERVAL_SECONDS,
            args.max_duration or DEFAULT_DAEMON_MAX_DURATION_SECONDS,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return
    elif args.command == "stop-daemon":
        result = {"ok": stop_daemon_process(args.pid)}
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
