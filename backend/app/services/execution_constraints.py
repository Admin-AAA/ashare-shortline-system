"""Compat shim: moved to app.engine.execution_constraints (engine must not depend on services)."""
import sys

from app.engine import execution_constraints as _impl

sys.modules[__name__] = _impl
