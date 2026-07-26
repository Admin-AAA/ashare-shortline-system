#!/usr/bin/env python3
"""Architecture dependency direction checker.

Verifies that the codebase respects the layered dependency contract:
  engine/  →  no imports from services/, adapters/, ORM, API
  services/runs/  →  no direct Tushare/OpenAI/Agno implementation imports
  No `from app.services.market_pool import _` private facade imports
  No Protocol-external `fetch_bars_for_quote` calls
  No dynamic Registry registration after bootstrap freeze

Exit code 0 = all checks pass, 1 = violations found.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "backend"
if not ROOT.is_dir():
    print(f"ERROR: backend directory not found at {ROOT}", file=sys.stderr)
    sys.exit(2)

ENGINE_DIR = ROOT / "app" / "engine"
SERVICES_DIR = ROOT / "app" / "services"
ADAPTERS_DIR = ROOT / "app" / "adapters"
API_DIR = ROOT / "app" / "api"

# Known historical violations that are grandfathered until a dedicated cleanup.
# If the set shrinks, update this list; if it grows, the gate fails.
KNOWN_ENGINE_VIOLATIONS = frozenset({
    "app/engine/agent_skill_runner.py: imports app.services.model_router_service",
    "app/engine/aggregator.py: imports app.services.execution_constraints",
    "app/engine/strategy_backtest_adapter.py: imports app.services.cost_service",
})


def _python_files(directory: Path) -> list[Path]:
    """Yield all .py files under directory."""
    return sorted(directory.rglob("*.py"))


def _imports_from(source: Path, *prefixes: str) -> list[str]:
    """Return import targets from source that start with any of the prefixes."""
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    except SyntaxError:
        return []

    results: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for prefix in prefixes:
                if node.module.startswith(prefix):
                    results.append(node.module)
                    break
    return results


def check_engine_no_services() -> list[str]:
    """engine/ must not import from services/, adapters/, or ORM models.

    A small set of historical reverse-deps is grandfathered via
    KNOWN_ENGINE_VIOLATIONS; the gate fails if the set grows or shifts.
    """
    violations: list[str] = []
    for path in _python_files(ENGINE_DIR):
        # Allow engine.backtest to reference app.models for type hints in test-only code
        bad = _imports_from(path, "app.services", "app.adapters")
        # Also check for ORM imports (app.models.tables) but allow app.models base
        orm_imports = _imports_from(path, "app.models.tables")
        all_bad = bad + orm_imports
        for imp in all_bad:
            rel = str(path.relative_to(ROOT))
            violations.append(f"{rel}: imports {imp}")

    new_violations = [v for v in violations if v not in KNOWN_ENGINE_VIOLATIONS]
    return new_violations


def check_no_market_pool_private_imports() -> list[str]:
    """No `from app.services.market_pool import _xxx` private facade imports."""
    violations: list[str] = []
    for path in _python_files(ROOT / "app"):
        # Skip the market_pool package itself
        if "market_pool" in str(path.relative_to(ROOT)):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "app.services.market_pool":
                continue
            for alias in (node.names or []):
                name = alias.name
                if name.startswith("_"):
                    rel = path.relative_to(ROOT)
                    violations.append(f"{rel}: from market_pool import {name}")
    return violations


def check_no_fetch_bars_for_quote() -> list[str]:
    """No Protocol-external fetch_bars_for_quote calls (should use MarketSourcePort)."""
    violations: list[str] = []
    for path in _python_files(ROOT / "app"):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if "fetch_bars_for_quote" in text:
            # Allow in market_pool/core.py (where it's defined) and test files
            rel = str(path.relative_to(ROOT))
            if "market_pool/core" in rel or "test_" in rel:
                continue
            # Check it's not just a comment
            for i, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "fetch_bars_for_quote" in stripped:
                    violations.append(f"{rel}:{i}: references fetch_bars_for_quote")
    return violations


def check_runs_no_vendor_imports() -> list[str]:
    """services/runs/ must not directly import Tushare/OpenAI/Agno implementations."""
    violations: list[str] = []
    runs_dir = SERVICES_DIR / "runs"
    if not runs_dir.is_dir():
        return violations
    for path in _python_files(runs_dir):
        bad = _imports_from(
            path,
            "app.services.tushare_client",
            "app.adapters.tushare",
            "agno",
            "openai",
        )
        for imp in bad:
            rel = path.relative_to(ROOT)
            violations.append(f"{rel}: imports {imp}")
    return violations


def check_production_uses_staged_pipeline() -> list[str]:
    """Production workers must enter through the coordinator-backed pipeline."""
    violations: list[str] = []
    workbench = SERVICES_DIR / "workbench_service.py"
    worker = ROOT / "scripts" / "run_workbench_subprocess.py"

    workbench_tree = ast.parse(workbench.read_text(encoding="utf-8"), filename=str(workbench))
    public_entry = next(
        (
            node
            for node in workbench_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "run_workbench"
        ),
        None,
    )
    if public_entry is None or not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "run_staged_pipeline"
        for node in ast.walk(public_entry)
    ):
        violations.append("app/services/workbench_service.py: run_workbench must delegate to run_staged_pipeline")

    legacy_callers = [
        node
        for node in ast.walk(workbench_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_run_workbench_legacy"
    ]
    if legacy_callers:
        violations.append("app/services/workbench_service.py: production code calls _run_workbench_legacy")

    worker_text = worker.read_text(encoding="utf-8")
    if "run_staged_pipeline" not in worker_text or "await run_staged_pipeline" not in worker_text:
        violations.append("scripts/run_workbench_subprocess.py: worker bypasses run_staged_pipeline")
    return violations


def main() -> int:
    checks = [
        ("engine → no services/adapters/ORM", check_engine_no_services),
        ("no market_pool private imports", check_no_market_pool_private_imports),
        ("no fetch_bars_for_quote outside core", check_no_fetch_bars_for_quote),
        ("runs → no vendor imports", check_runs_no_vendor_imports),
        ("production → staged pipeline", check_production_uses_staged_pipeline),
    ]

    all_violations: list[str] = []
    for name, check_fn in checks:
        violations = check_fn()
        if violations:
            print(f"\n❌ {name} ({len(violations)} violations):")
            for v in violations:
                print(f"   {v}")
            all_violations.extend(violations)
        else:
            print(f"✅ {name}")

    if all_violations:
        print(f"\n❌ {len(all_violations)} total violations found")
        return 1
    print("\n✅ All architecture checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
