"""Compat shim: moved to app.engine.return_improvement_shadow."""
import sys

from app.engine import return_improvement_shadow as _impl

sys.modules[__name__] = _impl
