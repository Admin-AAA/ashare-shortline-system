"""Process-wide deployment identity resolution shared by API, worker, and CLI.

The application identity feeds the recommendation ``execution_version`` hash,
so the enqueue side (FastAPI process) and the execution side (long-lived
Prefect worker) MUST resolve it identically. Historically only the worker
launcher pinned ``ASHARE_APPLICATION_REVISION`` at startup; an API launched
without that variable resolved ``"unknown"`` while the worker resolved the
pinned git SHA — every cross-process Run then failed the PREPARE-stage version
guard. This module gives every process the same resolution order:

explicit ``ASHARE_APPLICATION_REVISION`` env var -> cached ``git rev-parse HEAD``
-> canonical ``"unknown"``.
"""
from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

# backend/app/core/deployment_identity.py -> repository root.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_GIT_REVISION_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
_UNKNOWN_REVISION = "unknown"


def _git_head_revision() -> str:
    """Return a valid lowercase git HEAD SHA, or ``""`` when unavailable."""
    try:
        process = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    revision = process.stdout.strip() if process.returncode == 0 else ""
    return revision.lower() if _GIT_REVISION_RE.fullmatch(revision) else ""


@lru_cache(maxsize=1)
def _cached_git_head_revision() -> str:
    """Pin git HEAD once per process, exactly like a startup revision pin."""
    return _git_head_revision()


def resolve_application_revision(environ: Mapping[str, str] | None = None) -> str:
    """Resolve the deployed code revision with one order across processes.

    The explicit environment variable wins. When it is absent and the caller
    uses the live process environment, fall back to git HEAD, cached once per
    process, and mirror it into ``os.environ`` so subprocesses spawned by this
    process (e.g. Prefect process-worker flow runs) inherit the same value. An
    explicitly supplied ``environ`` mapping stays pure and never touches git so
    unit tests remain deterministic.
    """
    source = os.environ if environ is None else environ
    configured = str(source.get("ASHARE_APPLICATION_REVISION") or "").strip()
    if _GIT_REVISION_RE.fullmatch(configured):
        resolved = configured.lower()
    elif environ is None:
        resolved = _cached_git_head_revision() or _UNKNOWN_REVISION
    else:
        resolved = _UNKNOWN_REVISION

    if environ is None and not str(os.environ.get("ASHARE_APPLICATION_REVISION") or "").strip():
        os.environ["ASHARE_APPLICATION_REVISION"] = resolved
    return resolved


def resolve_application_version(environ: Mapping[str, str] | None = None) -> str:
    """Resolve the coarse application version (never a credential/URL)."""
    source = os.environ if environ is None else environ
    return str(source.get("ASHARE_APPLICATION_VERSION") or "development")


__all__ = [
    "resolve_application_revision",
    "resolve_application_version",
]
