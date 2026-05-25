from __future__ import annotations


RESUMABLE_STATUSES = {"interrupted", "failed"}


def progress_pct(current: int | None, total: int | None) -> float | None:
    current_value = int(current or 0)
    total_value = int(total or 0)
    if total_value <= 0:
        return None
    return round(min(100.0, max(0.0, current_value * 100 / total_value)), 2)


def is_resumable_status(status: str | None) -> bool:
    return str(status or "") in RESUMABLE_STATUSES
