"""Outbound ports the engine needs from the service layer.

Engine code must not import app.services. Services wire concrete adapters here
(see app/services/__init__.py, which runs whenever any app.services module is
imported). An unwired port raises a clear error instead of importing upward.
"""
from __future__ import annotations

from typing import Any, Callable

_ports: dict[str, Callable[..., Any]] = {}

ACTIVE_MODEL_ROUTES = "active_model_routes"
RECORD_TASK_RUN = "record_task_run"


def register(name: str, fn: Callable[..., Any]) -> None:
    _ports[name] = fn


def call(name: str, *args: Any, **kwargs: Any) -> Any:
    fn = _ports.get(name)
    if fn is None:
        raise RuntimeError(f"engine port {name!r} is not wired; import app.services first")
    return fn(*args, **kwargs)
