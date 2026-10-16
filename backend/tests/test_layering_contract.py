"""Layering contract: api -> services -> engine. Lower layers must not import upper.

AST based (includes function-local imports) so it runs without import-linter.
KNOWN_EXCEPTIONS lists the remaining debt; the list may only shrink.
"""
import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

FORBIDDEN = {
    "engine": ("app.services", "app.api"),
    "services": ("app.api",),
}
KNOWN_EXCEPTIONS: set = set()


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module
        elif isinstance(node, ast.Import):
            for a in node.names:
                yield a.name


def test_no_reverse_layer_imports():
    violations = set()
    for layer, banned in FORBIDDEN.items():
        for f in (APP / layer).rglob("*.py"):
            rel = f.relative_to(APP.parent).as_posix()
            for mod in _imports(f):
                if mod.startswith(banned):
                    violations.add((rel, mod))
    assert violations - KNOWN_EXCEPTIONS == set()
    assert KNOWN_EXCEPTIONS - violations == set(), "debt fixed: shrink KNOWN_EXCEPTIONS"
