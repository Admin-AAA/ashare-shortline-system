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
from hashlib import sha256
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

DEFAULT_MEMORY_DIR = Path.home() / ".claude/projects/-Users-wangao/memory"

HEARTBEAT_TIMEOUT_SECONDS = 30 * 60  # 30 min
DEFAULT_DAEMON_INTERVAL_SECONDS = 900  # 15 min
DEFAULT_DAEMON_MAX_DURATION_SECONDS = 3 * 3600  # bound abandoned daemons

# 前瞻（OOS）段最小已结算执行笔数（2026-08-02 软冻结修复）：publish 证据闸门
# 要求 canonical experiment 的 out_of_sample 段执行笔数 ≥ 此值，否则"改进"视为
# 小样本噪声禁止发布。对应系统诊断 Top1 行动项（self-improve 前瞻验证闸门）。
MIN_FORWARD_SAMPLES = 20

# Bump when the shape of self-improve-state.json changes meaningfully, so
# older tools/researchers can detect that they read a newer layout.
STATE_SCHEMA_VERSION = 4

RELEASE_TRANSITIONS = {
    ("draft", "verify_evidence"): "evidence_verified",
    ("completed", "verify_evidence"): "evidence_verified",
    ("rolled_back", "verify_evidence"): "evidence_verified",
    ("evidence_verified", "deployment_confirmed"): "canary_running",
    ("canary_running", "canary_passed"): "canary_passed",
    ("canary_running", "canary_failed"): "rollback_required",
    ("rollback_required", "rollback_confirmed"): "rolled_back",
    ("canary_passed", "champion_published"): "completed",
}

GOVERNANCE_PHASES = frozenset(
    {
        "audit",
        "candidate_ready",
        "canary_running",
        "canary_passed",
        "production",
        "rollback",
    }
)

# ``champion_published`` is an internal transition owned by publish_champion.
# Exposing it as a CLI event would let a caller skip the CAS/evidence checks.
CLI_RELEASE_EVENTS = frozenset(
    event for _status, event in RELEASE_TRANSITIONS
    if event != "champion_published"
)

RELEASE_ACTIVE_STATUSES = frozenset(
    {"evidence_verified", "canary_running", "canary_passed", "rollback_required"}
)
RELEASE_TERMINAL_STATUSES = frozenset({"completed", "rolled_back"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_governance_phase(
    state: dict,
    phase: str,
    details: dict[str, Any] | None = None,
) -> dict:
    """Atomically project release progress into the governance phase.

    ``audit_only`` remains in the state file for old readers, but it is no
    longer an approval input.  The phase is the source of truth and is only
    changed by this module's release transitions.
    """
    if phase not in GOVERNANCE_PHASES:
        raise ValueError(f"invalid governance phase: {phase}")
    governance = dict(state.get("selection_policy_governance") or {})
    governance.setdefault("mode", "autonomous")
    governance["phase"] = phase
    governance["audit_only"] = phase != "production"
    governance["phase_changed_at"] = _now_iso()
    if details:
        governance.update(details)
    state["selection_policy_governance"] = governance
    return governance


def _autonomous_canary_publish_allowed(
    state: dict,
    proposed_policy: dict | None,
    evidence_id: str | None,
) -> bool:
    """Return whether a candidate may exit audit through the verified Canary.

    This is intentionally narrow: a researcher cannot flip a boolean.  The
    release state must already contain the matching evidence, policy and
    Canary receipt, and governance must be explicitly autonomous.
    """
    governance = state.get("selection_policy_governance") or {}
    release = state.get("release_state") or {}
    return bool(
        governance.get("mode") == "autonomous"
        and governance.get("phase") == "canary_passed"
        and release.get("status") == "canary_passed"
        and evidence_id
        and release.get("evidence_id") == evidence_id
        and isinstance(proposed_policy, dict)
        and release.get("candidate_id") == _candidate_identity(
            str(evidence_id), str(proposed_policy.get("fingerprint") or "")
        )
        and release.get("policy_fingerprint") == proposed_policy.get("fingerprint")
        and release.get("deployment_adapter") == "local-policy-adapter:v1"
        and release.get("mode") == "active"
        and release.get("deployment_fingerprint")
        and release.get("receipt")
        and release.get("canary_evidence_id")
    )


def _candidate_identity(evidence_id: str, policy_fingerprint: str) -> str:
    return f"{evidence_id}:{policy_fingerprint}"


def _release_summary(release: dict) -> dict:
    return {
        key: release.get(key)
        for key in (
            "status",
            "evidence_id",
            "candidate_id",
            "policy_fingerprint",
            "owner_researcher_id",
            "deployment_fingerprint",
            "last_event",
            "updated_at",
        )
        if release.get(key) is not None
    }


def _reclaim_stale_release_slot(
    state: dict,
    stale_researcher_ids: set[str],
) -> dict | None:
    """Rollback and free a release slot whose owner has stopped heartbeating."""
    release = state.get("release_state") or {}
    owner = str(release.get("owner_researcher_id") or "")
    status = str(release.get("status") or "draft")
    if (
        status not in RELEASE_ACTIVE_STATUSES
        or (
            owner not in stale_researcher_ids
            and not release.get("reclaim_pending")
        )
    ):
        return None

    receipt = release.get("receipt")
    if receipt:
        from app.services.backtest.deployment_adapter import LocalPolicyDeploymentAdapter

        adapter = LocalPolicyDeploymentAdapter(
            Path(str(release.get("deployment_root") or ""))
            if release.get("deployment_root")
            else None
        )
        try:
            rollback_receipt = adapter.rollback(receipt)
        except Exception as exc:
            release["reclaim_pending"] = True
            release["reclaim_error"] = str(exc)
            state["release_state"] = release
            return {
                "error": "RELEASE_RECLAIM_BLOCKED",
                "owner_researcher_id": owner,
                "candidate_id": release.get("candidate_id"),
                "message": str(exc),
            }
    else:
        rollback_receipt = {"ok": True, "rolled_back_to": None}

    state.setdefault("release_history", []).append(_release_summary(release))
    state["release_state"] = {
        "status": "rolled_back",
        "last_event": "lease_expired",
        "updated_at": _now_iso(),
        "rollback_reason": "owner heartbeat stale",
        "rollback_receipt": rollback_receipt,
    }
    _set_governance_phase(
        state,
        "audit",
        {
            "candidate_evidence_id": release.get("evidence_id"),
            "candidate_policy_fingerprint": release.get("policy_fingerprint"),
            "last_rollback_reason": "owner heartbeat stale",
        },
    )
    return {
        "reclaimed": True,
        "owner_researcher_id": owner,
        "candidate_id": release.get("candidate_id"),
    }


def _release_transition_guard(
    state: dict,
    event: str,
    details: dict,
    researcher_id: str | None,
) -> dict | None:
    """Guard the single global release slot against concurrent researchers."""
    release = state.get("release_state") or {}
    status = str(release.get("status") or "draft")
    owner = str(researcher_id or details.get("researcher_id") or "")
    current_owner = str(release.get("owner_researcher_id") or "")

    if event == "verify_evidence":
        if status in RELEASE_ACTIVE_STATUSES:
            return {
                "error": "RELEASE_BUSY",
                "owner_researcher_id": current_owner or None,
                "candidate_id": release.get("candidate_id"),
                "status": status,
            }
        if status not in {"draft", *RELEASE_TERMINAL_STATUSES}:
            return {"error": "RELEASE_STATE_CONFLICT", "status": status}
        return None

    if status not in RELEASE_ACTIVE_STATUSES:
        return {"error": "RELEASE_STATE_CONFLICT", "status": status}
    if not current_owner:
        # Legacy states can finish their already-created release once. New
        # CLI transitions always carry a researcher_id and create ownership.
        return None
    if not owner:
        return {
            "error": "RELEASE_OWNER_REQUIRED",
            "owner_researcher_id": current_owner,
        }
    if owner != current_owner:
        return {
            "error": "RELEASE_NOT_OWNER",
            "owner_researcher_id": current_owner,
            "candidate_id": release.get("candidate_id"),
        }

    expected_policy = str(release.get("policy_fingerprint") or "")
    supplied_policy = str(
        details.get("candidate_policy_fingerprint")
        or details.get("deployed_policy_fingerprint")
        or details.get("observed_policy_fingerprint")
        or ""
    )
    if supplied_policy and expected_policy and supplied_policy != expected_policy:
        return {
            "error": "RELEASE_CANDIDATE_MISMATCH",
            "expected_policy_fingerprint": expected_policy,
            "actual_policy_fingerprint": supplied_policy,
        }
    supplied_evidence = str(details.get("candidate_evidence_id") or "")
    expected_evidence = str(release.get("evidence_id") or "")
    if supplied_evidence and expected_evidence and supplied_evidence != expected_evidence:
        return {
            "error": "RELEASE_CANDIDATE_MISMATCH",
            "expected_evidence_id": expected_evidence,
            "actual_evidence_id": supplied_evidence,
        }
    return None


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


_FORBIDDEN_RESEARCH_STATE_KEYS = frozenset(
    {"canonical_production", "selection_policy_governance", "release_state", "researcher_registry"}
)


def _current_policy_identity() -> dict[str, Any]:
    """Resolve the repo-owned policy identity without copying policy rules."""
    from app.engine.production_policy import DEFAULT_PRODUCTION_SELECTION_POLICY

    policy = DEFAULT_PRODUCTION_SELECTION_POLICY
    return {
        "policy_id": policy.policy_id,
        "version": policy.version,
        "fingerprint": policy.fingerprint,
    }


def _identity_context() -> dict[str, Any]:
    """Code-revision context of the tree this state tool is executing from.

    The policy fingerprint is computed from the imported ``app`` package,
    which is whatever tree this script file lives in — NOT necessarily main
    and NOT necessarily clean. With several parallel instances sharing one
    state file, ``policy_artifact_mismatch`` flaps when a tool run happens to
    execute from a tree whose uncommitted WIP changes ``canonical_payload()``
    (observed 2026-09-13: main eb78093 identity 8c4821b9 vs main-tree
    feature-branch+WIP identity 9707a4ca for the same v3 artifact). Recording
    the executing tree per health check / reanchor makes those flaps
    auditable instead of silent.
    """
    tree_root = Path(__file__).resolve().parents[2]
    context: dict[str, Any] = {"tree_root": str(tree_root), "recorded_at": _now_iso()}
    try:
        commit = subprocess.run(
            ["git", "-C", str(tree_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(tree_root), "status", "--porcelain", "--", "backend"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        context["commit"] = commit
        context["backend_dirty"] = bool(dirty)
        main_tip = ""
        for ref in ("main", "origin/main"):
            probe = subprocess.run(
                ["git", "-C", str(tree_root), "rev-parse", "--verify", "--quiet", ref],
                capture_output=True,
                text=True,
                check=False,
            )
            if probe.returncode == 0:
                main_tip = probe.stdout.strip()
                if main_tip:
                    break
        context["main_tip"] = main_tip or None
        # production-owned: executing tree is parked exactly at the main
        # branch tip. A clean worktree on a stale/diverged branch computes a
        # *different but commited* fingerprint which is not the production
        # truth (observed 2026-09-13: clean worktree on feat/si-fina-
        # indicator-pit @31759cf recomputed the v3 identity from pre-merge
        # code and flipped policy_artifact_mismatch back to true).
        context["production_owned"] = bool(
            commit and main_tip and commit == main_tip
        )
    except Exception as exc:  # git unavailable or not a work tree
        context["error"] = str(exc)
    return context


def _resolve_policy_identity(
    policy_loader: Callable[[], dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Resolve policy identity plus the executing-tree context.

    Returns ``(identity, identity_context, repo_owned)``. ``repo_owned`` is
    True only when the executing tree is clean AND parked exactly at the
    main branch tip — i.e. its fingerprint is the production-owned truth.
    Two failure modes must not rebind governance state:

    - dirty tree (T-102, 2026-09-13): the fingerprint reflects uncommitted
      WIP rather than any committed artifact (main-tree feature branch +
      WIP produced 9707a4ca vs main's 8c4821b9 for the same v3 artifact).
    - clean but diverged tree (T-104, 2026-09-13): a worktree parked on a
      stale branch is clean, yet its identity reflects that branch's code,
      not production. ``feat/si-fina-indicator-pit`` @31759cf recomputed the
      v3 identity from pre-merge code and flipped
      ``policy_artifact_mismatch`` back to true, reopening the T-098
      deadlock.
    """
    context = _identity_context()
    identity = dict((policy_loader or _current_policy_identity)())
    repo_owned = (
        context.get("backend_dirty") is False
        and context.get("production_owned") is True
        and "error" not in context
    )
    return identity, context, repo_owned


def migrate_state(
    memory_dir: Path | None = None,
    *,
    policy_loader: Callable[[], dict[str, Any]] | None = None,
) -> dict:
    """Idempotently migrate legacy self-improve state to schema v3.

    The migration only replaces copied policy rules with the identity of the
    repository artifact and initializes autonomous governance.  It never
    changes the champion commit or publishes a strategy.
    """
    directory = memory_dir or _memory_dir()
    state_path = directory / "self-improve-state.json"
    with _locked(directory):
        state = _read_json(state_path)
        identity, identity_ctx, repo_owned = _resolve_policy_identity(policy_loader)
        if set(identity) != {"policy_id", "version", "fingerprint"}:
            raise ValueError("policy_loader must return policy_id/version/fingerprint")
        champion = dict(state.get("canonical_production") or {})
        legacy_rules = champion.pop("rules", None)
        if legacy_rules is not None:
            legacy_payload = json.dumps(
                legacy_rules, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            state["state_migration"] = {
                "from_schema_version": state.get("schema_version", 0),
                "migrated_at": _now_iso(),
                "legacy_rules_fingerprint": sha256(legacy_payload).hexdigest(),
            }
        existing = champion.get("selection_policy")
        needs_bind = not isinstance(existing, dict) or set(existing) != set(identity)
        if needs_bind and (repo_owned or not isinstance(existing, dict)):
            # 缺失/畸形时总是补绑（bootstrap 依赖它）；已注册身份只在干净树上
            # 重绑——脏树的 WIP 指纹不得覆盖既有注册。
            champion["selection_policy"] = identity
        else:
            state.setdefault("state_migration", {"migrated_at": _now_iso()})
        state["canonical_production"] = champion
        governance = dict(state.get("selection_policy_governance") or {})
        governance.setdefault("mode", "autonomous")
        phase = governance.get("phase")
        if phase not in GOVERNANCE_PHASES:
            phase = "production" if governance.get("audit_only") is False else "audit"
        governance["phase"] = phase
        governance["audit_only"] = phase != "production"
        governance.setdefault("strategy_inactive", False)
        if repo_owned:
            governance.setdefault("policy_artifact_fingerprint", identity["fingerprint"])
            governance["policy_artifact_mismatch"] = (
                governance.get("policy_artifact_fingerprint") != identity["fingerprint"]
                or champion.get("selection_policy", {}).get("fingerprint") != identity["fingerprint"]
            )
        governance["identity_context"] = identity_ctx
        state["selection_policy_governance"] = governance
        state.setdefault("release_state", {"status": "draft"})
        state.setdefault("holdout_ledger", {"consumed": {}})
        state["schema_version"] = STATE_SCHEMA_VERSION
        _atomic_write_json(state_path, state)
        return state


def reanchor_policy_identity(
    memory_dir: Path | None = None,
    *,
    from_fingerprint: str,
    reason: str,
    researcher_id: str | None = None,
    policy_loader: Callable[[], dict[str, Any]] | None = None,
) -> dict:
    """CAS-guarded re-anchor of the stored policy identity to the repo artifact.

    Policy fingerprints are code-revision dependent: ``canonical_payload()``
    evolves with the ``ProductionSelectionPolicy`` dataclass, so a champion
    registered under an older code revision can never match the current
    identity even when no artifact rule changed. ``migrate_state`` only
    ``setdefault``s the governance fingerprint and ``publish_champion`` is
    gated on ``policy_artifact_mismatch=False``, which deadlocks the release
    state machine after any landing that changes the payload (e.g. the v3
    merge). This command is the explicit escape hatch: it re-binds
    ``canonical_production.selection_policy`` and
    ``selection_policy_governance.policy_artifact_fingerprint`` to the
    repository-owned artifact identity, records the transition, and never
    touches champion metrics / commit / release state.
    """
    if not from_fingerprint or not reason:
        raise ValueError("--from-fingerprint and --reason are required")
    directory = memory_dir or _memory_dir()
    state_path = directory / "self-improve-state.json"
    with _locked(directory):
        state = _read_json(state_path)
        identity, identity_ctx, repo_owned = _resolve_policy_identity(policy_loader)
        if set(identity) != {"policy_id", "version", "fingerprint"}:
            raise ValueError("policy_loader must return policy_id/version/fingerprint")
        if not repo_owned:
            return {
                "ok": False,
                "error": "DIRTY_TREE",
                "message": (
                    "reanchor 拒绝在脏执行树上运行：当前树 backend 存在未提交改动"
                    "（或 git 不可用），其指纹不是 repo-owned 真值，"
                    "重锚会把 WIP 指纹写入治理状态。请切到干净 main 树后重试。"
                ),
                "identity_context": identity_ctx,
            }
        champion = dict(state.get("canonical_production") or {})
        governance = dict(state.get("selection_policy_governance") or {})
        stored_gov_fp = str(governance.get("policy_artifact_fingerprint") or "")
        stored_champ = dict(champion.get("selection_policy") or {})
        if identity["fingerprint"] == from_fingerprint:
            return {
                "ok": False,
                "error": "ALREADY_ANCHORED",
                "message": "当前 repo 政策指纹与 --from-fingerprint 相同，无需重锚",
            }
        if stored_gov_fp != from_fingerprint:
            return {
                "ok": False,
                "error": "CAS_MISMATCH",
                "message": (
                    "governance.policy_artifact_fingerprint="
                    f"{stored_gov_fp!r} != --from-fingerprint {from_fingerprint!r}"
                ),
            }
        reanchor_record = {
            "at": _now_iso(),
            "from_fingerprint": from_fingerprint,
            "to_fingerprint": identity["fingerprint"],
            "from_selection_policy": stored_champ,
            "to_selection_policy": dict(identity),
            "reason": reason,
            "researcher_id": researcher_id,
            "identity_context": identity_ctx,
        }
        champion["selection_policy"] = dict(identity)
        champion["policy_reanchor"] = reanchor_record
        governance["policy_artifact_fingerprint"] = identity["fingerprint"]
        governance["policy_artifact_mismatch"] = (
            governance.get("policy_artifact_fingerprint") != identity["fingerprint"]
            or champion.get("selection_policy", {}).get("fingerprint") != identity["fingerprint"]
        )
        migration = dict(state.get("state_migration") or {})
        history = list(migration.get("policy_reanchor_history") or [])
        history.append(reanchor_record)
        migration["policy_reanchor_history"] = history
        migration["policy_reanchored_at"] = reanchor_record["at"]
        state["state_migration"] = migration
        state["canonical_production"] = champion
        state["selection_policy_governance"] = governance
        _atomic_write_json(state_path, state)
        return {
            "ok": True,
            "policy_artifact_mismatch": governance["policy_artifact_mismatch"],
            "reanchor": reanchor_record,
        }


def _reject_direct_research_state_patch(state_patch: dict) -> None:
    forbidden = sorted(_FORBIDDEN_RESEARCH_STATE_KEYS.intersection(state_patch))
    if forbidden:
        raise ValueError(
            "direct state mutation is disabled for " + ", ".join(forbidden)
        )


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
    _reject_direct_research_state_patch(state_patch)

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

        stale_workers = _purge_stale_workers(active)
        _release_claimed_directions(state, list(stale_workers.values()))
        reclaim = _reclaim_stale_release_slot(state, set(stale_workers))
        if reclaim and reclaim.get("error"):
            _atomic_write_json(state_path, state)
            return reclaim

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
        release = state.get("release_state") or {}
        if (
            str(release.get("owner_researcher_id") or "") == researcher_id
            and str(release.get("status") or "") in RELEASE_ACTIVE_STATUSES
        ):
            return {
                "error": "RELEASE_ACTIVE",
                "status": release.get("status"),
                "candidate_id": release.get("candidate_id"),
                "message": "release pipeline 未完成，不能释放 researcher direction",
            }
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
        stale_workers = _purge_stale_workers(active)
        _release_claimed_directions(state, list(stale_workers.values()))
        reclaim = _reclaim_stale_release_slot(state, set(stale_workers))
        if reclaim and reclaim.get("error"):
            _atomic_write_json(state_path, state)
            return reclaim

        active_dirs = {
            str(info.get("claimed_direction", ""))
            for info in active.values()
            if isinstance(info, dict)
        }
        released = [
            d for d in claimed
            if isinstance(d, str) and d not in active_dirs
        ]
        if released or reclaim:
            registry["claimed_directions"] = [
                d for d in claimed
                if not (isinstance(d, str) and d in set(released))
            ]
            _atomic_write_json(state_path, state)
        return {"ok": True, "released": released, "release_reclaimed": bool(reclaim)}


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


def transition_release_state(current: dict, event: str, details: dict | None = None) -> dict:
    """Pure release/deploy/canary/rollback state transition."""
    state = dict(current or {"status": "draft"})
    status = str(state.get("status") or "draft")
    next_status = RELEASE_TRANSITIONS.get((status, str(event)))
    if next_status is None:
        raise ValueError(f"invalid release transition: {status} --{event}-->")
    if event == "verify_evidence":
        evidence = details or {}
        required = ("evidence_id", "evidence_fingerprint", "policy_fingerprint")
        missing = [key for key in required if not str(evidence.get(key) or "")]
        if missing:
            raise ValueError("verify_evidence requires " + ", ".join(missing))
        if evidence.get("evidence_status") != "completed" or evidence.get("should_publish") is not True:
            raise ValueError("only completed should_publish canonical evidence can be verified")
    if event == "deployment_confirmed":
        deployment = details or {}
        required = ("deployment_adapter", "deployment_fingerprint", "deployed_policy_fingerprint")
        missing = [key for key in required if not str(deployment.get(key) or "")]
        if missing:
            raise ValueError(
                "deployment_confirmed requires " + ", ".join(missing)
            )
        from app.services.backtest.deployment_adapter import LocalPolicyDeploymentAdapter

        receipt = deployment.get("receipt") or deployment
        verified_receipt = LocalPolicyDeploymentAdapter.verify_receipt(receipt)
        if deployment.get("deployment_fingerprint") != verified_receipt["deployment_fingerprint"]:
            raise ValueError("deployment receipt fingerprint mismatch")
        expected = str(state.get("policy_fingerprint") or "")
        if expected and deployment["deployed_policy_fingerprint"] != expected:
            raise ValueError("deployed policy fingerprint does not match verified evidence")
    if event == "canary_passed":
        canary = details or {}
        required = ("canary_evidence_id", "observed_policy_fingerprint")
        missing = [key for key in required if not str(canary.get(key) or "")]
        if missing:
            raise ValueError("canary_passed requires " + ", ".join(missing))
        if int(canary.get("error_count") or 0) != 0:
            raise ValueError("canary error_count must be zero")
        if canary.get("coverage_gate_passed") is not True or canary.get("productive_day_gate_passed") is not True:
            raise ValueError("canary coverage and productive-day gates must pass")
        expected = str(state.get("policy_fingerprint") or "")
        if expected and canary.get("observed_policy_fingerprint") != expected:
            raise ValueError("canary observed policy fingerprint mismatch")
    if event == "rollback_confirmed" and not details:
        raise ValueError("rollback_confirmed requires rollback receipt")
    updated = {
        **state,
        "status": next_status,
        "updated_at": _now_iso(),
        "last_event": str(event),
    }
    if details:
        updated.update(details)
    return updated


def advance_release(
    event: str,
    details: dict | None = None,
    memory_dir: Path | None = None,
    evidence_loader: Callable[[str], dict] | None = None,
    researcher_id: str | None = None,
) -> dict:
    directory = memory_dir or _memory_dir()
    state_path = directory / "self-improve-state.json"
    with _locked(directory):
        state = _read_json(state_path)
        normalized_details = dict(details or {})
        conflict = _release_transition_guard(
            state, event, normalized_details, researcher_id
        )
        if conflict is not None:
            return conflict
        if event == "verify_evidence":
            evidence_id = str(normalized_details.get("evidence_id") or "")
            if not evidence_id:
                raise ValueError("verify_evidence requires evidence_id")
            evidence = (evidence_loader or _load_canonical_evidence_from_db)(evidence_id)
            decision = evidence.get("decision") or {}
            normalized_details.update(
                {
                    "evidence_id": evidence_id,
                    "evidence_fingerprint": str(evidence.get("evidence_fingerprint") or ""),
                    "policy_fingerprint": str(
                        evidence.get("challenger_policy_fingerprint") or ""
                    ),
                    "evidence_status": str(evidence.get("status") or ""),
                    "should_publish": decision.get("should_publish") is True,
                }
            )
            policy_fingerprint = str(
                normalized_details.get("policy_fingerprint") or ""
            )
            normalized_details["candidate_id"] = _candidate_identity(
                evidence_id, policy_fingerprint
            )
            if researcher_id:
                normalized_details["owner_researcher_id"] = researcher_id
        if event == "canary_failed":
            release = state.get("release_state") or {}
            receipt = release.get("receipt")
            if receipt:
                from app.services.backtest.deployment_adapter import LocalPolicyDeploymentAdapter

                adapter = LocalPolicyDeploymentAdapter(
                    Path(str(release.get("deployment_root") or ""))
                    if release.get("deployment_root")
                    else None
                )
                normalized_details["rollback_receipt"] = adapter.rollback(receipt)
                normalized_details["rollback_confirmed"] = True
        current_release = state.get("release_state") or {}
        if event == "verify_evidence" and str(current_release.get("status")) in RELEASE_TERMINAL_STATUSES:
            history = state.setdefault("release_history", [])
            history.append(
                {
                    key: current_release.get(key)
                    for key in (
                        "status",
                        "evidence_id",
                        "policy_fingerprint",
                        "owner_researcher_id",
                        "deployment_fingerprint",
                        "last_event",
                        "updated_at",
                    )
                    if current_release.get(key) is not None
                }
            )
            current_release = {"status": current_release.get("status")}
        release = transition_release_state(
            current_release, event, normalized_details
        )
        state["release_state"] = release
        phase_by_event = {
            "verify_evidence": "candidate_ready",
            "deployment_confirmed": "canary_running",
            "canary_passed": "canary_passed",
            "canary_failed": "rollback",
            "rollback_confirmed": "audit",
            # Kept for the pure transition helper and old callers; the CLI
            # does not expose this event, and publish_champion owns it.
            "champion_published": "production",
        }
        phase = phase_by_event.get(event)
        if phase is not None:
            _set_governance_phase(
                state,
                phase,
                {
                    "candidate_evidence_id": release.get("evidence_id"),
                    "candidate_policy_fingerprint": release.get("policy_fingerprint"),
                },
            )
        _atomic_write_json(state_path, state)
        return release


def summarize_policy_health(rows: list[tuple[Any, Any, Any]]) -> dict:
    """Derive liveness from latest formal runs without treating no-opportunity as zero."""
    from app.services.runs.opportunity import audit_has_opportunity

    by_date: dict[str, dict[str, bool]] = {}
    for trade_date, recommended_count, raw_audit in rows:
        day = str(trade_date)
        state = by_date.setdefault(
            day, {"opportunity": False, "active": False, "policy_blocked": False}
        )
        state["active"] = state["active"] or int(recommended_count or 0) > 0
        try:
            audit = json.loads(raw_audit) if isinstance(raw_audit, str) else (raw_audit or {})
        except (TypeError, json.JSONDecodeError):
            audit = {}
        if audit_has_opportunity(audit):
            state["opportunity"] = True
        if str(audit.get("empty_reason") or "").startswith("production_policy_blocked:"):
            state["policy_blocked"] = True
    opportunity_days = [
        (day, values)
        for day, values in sorted(by_date.items(), reverse=True)
        if values["opportunity"]
    ][:5]
    inactive = len(opportunity_days) == 5 and all(
        not values["active"] and values["policy_blocked"]
        for _day, values in opportunity_days
    )
    return {
        "strategy_inactive": inactive,
        "recent_opportunity_days": [
            {"trade_date": day, **values} for day, values in opportunity_days
        ],
    }


def _load_policy_health_from_db() -> dict:
    from sqlalchemy import text
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        rows = db.execute(text("""
            SELECT trade_date, recommended_count, filter_audit_json
            FROM recommendation_runs
            WHERE run_mode='formal' AND status='completed'
            ORDER BY trade_date DESC, finished_at DESC
            LIMIT 200
        """)).fetchall()
    return summarize_policy_health(list(rows))


def refresh_policy_health(
    memory_dir: Path | None = None,
    health_loader: Callable[[], dict] | None = None,
) -> dict:
    health = (health_loader or _load_policy_health_from_db)()
    directory = memory_dir or _memory_dir()
    state_path = directory / "self-improve-state.json"
    with _locked(directory):
        state = _read_json(state_path)
        governance = dict(state.get("selection_policy_governance") or {})
        governance.setdefault("mode", "autonomous")
        phase = governance.get("phase")
        if phase not in GOVERNANCE_PHASES:
            phase = "production" if governance.get("audit_only") is False else "audit"
        governance["phase"] = phase
        governance["audit_only"] = phase != "production"
        governance.update(health)
        identity, identity_ctx, repo_owned = _resolve_policy_identity()
        if repo_owned:
            # Clean-tree self-heal: recompute the mismatch flag against the
            # repo-owned identity so a dirty-tree run from another parallel
            # instance cannot leave a stale/poisoned flag behind.
            champion = dict(state.get("canonical_production") or {})
            governance["policy_artifact_mismatch"] = (
                governance.get("policy_artifact_fingerprint") != identity["fingerprint"]
                or champion.get("selection_policy", {}).get("fingerprint") != identity["fingerprint"]
            )
        governance["identity_context"] = identity_ctx
        governance["health_checked_at"] = _now_iso()
        state["selection_policy_governance"] = governance
        _atomic_write_json(state_path, state)
        return governance


def _selection_policy_from_patch(state_patch: dict) -> dict | None:
    production = state_patch.get("canonical_production")
    if not isinstance(production, dict):
        return None
    policy = production.get("selection_policy")
    return policy if isinstance(policy, dict) else None


def _load_canonical_evidence_from_db(experiment_id: str) -> dict:
    """Read authoritative evidence; callers cannot supply a replacement JSON."""
    from app.db.session import SessionLocal
    from app.models.tables import BacktestExperiment

    with SessionLocal() as db:
        row = db.get(BacktestExperiment, experiment_id)
        if row is None or not row.conclusion_json:
            raise ValueError("canonical evidence not found")
        value = json.loads(str(row.conclusion_json))
        if not isinstance(value, dict):
            raise ValueError("canonical evidence conclusion must be an object")
        return value


def _validate_selection_policy_publish(
    *,
    state: dict,
    proposed_policy: dict,
    evidence_id: str | None,
    evidence_loader: Callable[[str], dict] | None,
) -> dict | None:
    required_policy_keys = {"policy_id", "version", "fingerprint"}
    if set(proposed_policy) != required_policy_keys:
        return {
            "error": "SELECTION_POLICY_STATE_CONTRACT",
            "message": "state 只能保存 policy_id/version/fingerprint",
        }
    governance = state.get("selection_policy_governance") or {}
    if bool(governance.get("audit_only", True)) and not _autonomous_canary_publish_allowed(
        state, proposed_policy, evidence_id
    ):
        return {
            "error": "SELECTION_POLICY_AUDIT_ONLY",
            "message": "selection policy 尚未通过 autonomous Canary；只允许 FINDING",
        }
    if bool(governance.get("strategy_inactive")):
        return {
            "error": "STRATEGY_INACTIVE",
            "message": "最近 5 个 opportunity days 全部零票，Champion 发布已冻结",
        }
    if not evidence_id:
        return {"error": "CANONICAL_EVIDENCE_REQUIRED"}
    try:
        evidence = (evidence_loader or _load_canonical_evidence_from_db)(evidence_id)
    except Exception as exc:
        return {"error": "CANONICAL_EVIDENCE_NOT_FOUND", "message": str(exc)}
    decision = evidence.get("decision") or {}
    gates = decision.get("gates") or {}
    from app.services.backtest.policy_experiment import RELEASE_POLICY_FINGERPRINT
    from app.services.backtest.release_policy import REQUIRED_RELEASE_GATES
    current_policy = (
        state.get("canonical_production", {}).get("selection_policy") or {}
    )
    governance_policy_fingerprint = str(
        governance.get("policy_artifact_fingerprint") or ""
    )
    checks = {
        "schema_v2": evidence.get("schema_version") == 2,
        "production_policy_kind": evidence.get("experiment_kind") == "production_policy",
        "completed": evidence.get("status") == "completed",
        "should_publish": decision.get("should_publish") is True,
        "candidate_decision": decision.get("decision") == "PRODUCTION_CANDIDATE",
        "single_rule_change": gates.get("single_rule_change") is True,
        "all_release_gates": bool(gates) and all(value is True for value in gates.values()),
        "release_gate_schema": set(gates) == set(REQUIRED_RELEASE_GATES),
        "champion_fingerprint": evidence.get("champion_policy_fingerprint") == current_policy.get("fingerprint"),
        "policy_artifact_identity": (
            not bool(governance.get("policy_artifact_mismatch"))
            and current_policy.get("fingerprint") == governance_policy_fingerprint
        ),
        "challenger_fingerprint": evidence.get("challenger_policy_fingerprint") == proposed_policy.get("fingerprint"),
        "changed_rule_id": bool(evidence.get("changed_rule_id")),
        "release_policy_fingerprint": (
            evidence.get("release_policy_fingerprint") == RELEASE_POLICY_FINGERPRINT
        ),
    }
    # 前瞻样本最小量门槛（2026-08-02 软冻结修复）：OOS 段已结算执行笔数必须
    # ≥ MIN_FORWARD_SAMPLES，否则"改进"只是小样本噪声，禁止发布。与
    # oos_executed_trade_required（>0）互补：存在性之外还要有统计意义。
    # 读取路径兼容 policy_experiment 实际落库结构（arms.challenger.split_coverage_evidence）
    # 与旧顶层结构（split_coverage_evidence）。
    _challenger_arm = (evidence.get("arms") or {}).get("challenger") or {}
    _oos_coverage = (
        ((_challenger_arm.get("split_coverage_evidence") or {})
         .get("out_of_sample") or {})
        or ((evidence.get("split_coverage_evidence") or {}).get("out_of_sample") or {})
    )
    checks["forward_samples_minimum"] = (
        int(_oos_coverage.get("executed_trades") or 0) >= MIN_FORWARD_SAMPLES
    )
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        return {"error": "CANONICAL_EVIDENCE_REJECTED", "failed_checks": failed}
    release = state.get("release_state") or {}
    if (
        release.get("status") != "canary_passed"
        or release.get("evidence_id") != evidence_id
        or release.get("policy_fingerprint") != proposed_policy.get("fingerprint")
        or release.get("candidate_id") != _candidate_identity(
            str(evidence_id), str(proposed_policy.get("fingerprint") or "")
        )
        or release.get("deployment_adapter") != "local-policy-adapter:v1"
        or release.get("mode") != "active"
        or not release.get("deployment_fingerprint")
        or not release.get("receipt")
        or not release.get("canary_evidence_id")
    ):
        return {"error": "CANARY_PASS_REQUIRED"}
    return None


def publish_champion(
    researcher_id: str,
    expected_champion_commit: str,
    state_patch: dict,
    run_record: dict | None = None,
    memory_dir: Path | None = None,
    canonical_evidence_id: str | None = None,
    evidence_loader: Callable[[str], dict] | None = None,
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

        proposed_policy = _selection_policy_from_patch(state_patch)
        release = state.get("release_state") or {}
        release_owner = str(release.get("owner_researcher_id") or "")
        if proposed_policy is not None and release_owner and release_owner != researcher_id:
            return {
                "error": "RELEASE_NOT_OWNER",
                "owner_researcher_id": release_owner,
                "candidate_id": release.get("candidate_id"),
            }
        if "canonical_production" in state_patch:
            governance = state.get("selection_policy_governance") or {}
            if bool(governance.get("audit_only", True)) and not _autonomous_canary_publish_allowed(
                state, proposed_policy, canonical_evidence_id
            ):
                return {
                    "error": "SELECTION_POLICY_AUDIT_ONLY",
                    "message": "audit 阶段禁止 production champion 变更；请先完成 autonomous Canary",
                }

        if proposed_policy is not None:
            evidence_error = _validate_selection_policy_publish(
                state=state,
                proposed_policy=proposed_policy,
                evidence_id=canonical_evidence_id,
                evidence_loader=evidence_loader,
            )
            if evidence_error is not None:
                return evidence_error

        guard = _encode_provisional_guard(
            state_patch, state.get("canonical_production", {})
        )
        if guard is not None:
            key, message = guard
            return {"error": key, "message": message}

        _apply_state_patch(state, state_patch)

        if proposed_policy is not None:
            state["release_state"] = transition_release_state(
                state.get("release_state") or {},
                "champion_published",
                {"published_by": researcher_id},
            )
            governance = state.get("selection_policy_governance") or {}
            if governance.get("mode") == "autonomous":
                _set_governance_phase(
                    state,
                    "production",
                    {
                        "active_policy_fingerprint": proposed_policy.get("fingerprint"),
                        "last_auto_promotion_at": _now_iso(),
                    },
                )

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
    forbidden = sorted(
        _FORBIDDEN_RESEARCH_STATE_KEYS.intersection(state_patch)
        - {"canonical_production"}
    )
    if forbidden:
        raise ValueError(
            "publish_finding cannot mutate " + ", ".join(forbidden)
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
    subparsers.add_parser("refresh-policy-health")
    subparsers.add_parser("migrate-state")

    reanchor_parser = subparsers.add_parser("reanchor-policy")
    reanchor_parser.add_argument("--from-fingerprint", required=True)
    reanchor_parser.add_argument("--reason", required=True)
    reanchor_parser.add_argument("--researcher-id", default=None)

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
    pub_champ.add_argument("--canonical-evidence-id")

    release = subparsers.add_parser("advance-release")
    release.add_argument("--researcher-id", required=True)
    release.add_argument(
        "--event",
        required=True,
        choices=sorted(CLI_RELEASE_EVENTS),
    )
    release.add_argument("--details", type=_json_object, default={})

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
    elif args.command == "refresh-policy-health":
        result = refresh_policy_health()
    elif args.command == "migrate-state":
        result = migrate_state()
    elif args.command == "reanchor-policy":
        result = reanchor_policy_identity(
            from_fingerprint=args.from_fingerprint,
            reason=args.reason,
            researcher_id=args.researcher_id,
        )
    elif args.command == "commit":
        result = {
            "error": "DIRECT_STATE_MUTATION_DISABLED",
            "message": "commit 已退役；请使用 publish-finding 或受控发布状态机",
        }
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
            canonical_evidence_id=args.canonical_evidence_id,
        )
    elif args.command == "advance-release":
        result = advance_release(
            args.event,
            args.details,
            researcher_id=args.researcher_id,
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
