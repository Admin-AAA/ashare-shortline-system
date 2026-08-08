"""Canonical opportunity semantics for persisted run audits.

The production-policy replay evaluates opportunity from scored cards before the
selection policy runs.  A persisted ``filter_audit_json`` is the read-only
projection of those cards used by health diagnostics; its score-line stage's
``input_count`` is only the number of scored cards and is not an opportunity
count.
"""
from __future__ import annotations

from typing import Any, Mapping


def audit_has_opportunity(audit: Mapping[str, Any] | None) -> bool:
    """Return whether a persisted audit contains a canonical opportunity card.

    This mirrors ``backtest.policy_experiment._is_opportunity_day`` using the
    fields retained by ``filter_audit_json``.  Missing card-level evidence is
    conservatively treated as not an opportunity rather than inferred from the
    score-line stage count.
    """
    if not isinstance(audit, Mapping):
        return False
    for raw in audit.get("rows") or ():
        if not isinstance(raw, Mapping):
            continue
        score_line = raw.get("score_line")
        if not isinstance(score_line, Mapping):
            continue
        meta = score_line.get("score_line_meta")
        if not isinstance(meta, Mapping):
            continue
        base_score_line = meta.get("base_score_line")
        final_score = score_line.get("final_score")
        try:
            if base_score_line is None or float(final_score or 0) < float(base_score_line):
                continue
            if float(raw.get("price") or raw.get("recommend_price") or 0) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        if bool(raw.get("hard_veto")):
            continue
        if bool(meta.get("regime_struct_strong_hard_veto")) or bool(
            meta.get("regime_oscillation_hard_veto")
        ):
            continue
        execution_status = str(meta.get("execution_status") or "").lower()
        if execution_status not in {"tradable", "risky"}:
            continue
        if bool(meta.get("execution_blocked")) or execution_status == "blocked":
            continue
        return True
    return False
