"""Process-local cache invalidation hooks.

Lets services invalidate caches owned by the API layer without importing
app.api (which would invert the layering). The API module registers its hook
on import; in processes where it is never imported the cache never exists, so
a missing hook is a no-op, exactly as before.
"""
from __future__ import annotations

from typing import Callable

_hooks: dict[str, list[Callable[[], None]]] = {}


def register(name: str, fn: Callable[[], None]) -> None:
    lst = _hooks.setdefault(name, [])
    if fn not in lst:
        lst.append(fn)


def invalidate(name: str) -> None:
    for fn in list(_hooks.get(name, ())):
        fn()
