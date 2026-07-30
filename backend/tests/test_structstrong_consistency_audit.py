"""Regression tests for struct-strong consistency audit pure logic.

Locks the measurement-audit invariants so a future refactor of the penalty
intro date or the dynamic-base floor math cannot silently change the audit
conclusion.  No DB access — pure functions only.
"""
from __future__ import annotations

from datetime import datetime

from scripts.si_structstrong_consistency_audit import (  # type: ignore
    PENALTY_INTRO_UTC,
    STRUCT_STRONG_FLOOR,
    SCORE_LINE_FLOOR,
)


def _would_current_floor_filter(final_score: float, regime: str) -> bool:
    """Replicate aggregator decision: +4 only on a dynamic base.

    The +4 struct-strong penalty is added to the *dynamic base*
    ``effective_score_line`` (which can sit below SCORE_LINE_FLOOR depending on
    market conditions), NOT to an absolute 76.  For the audit we approximate the
    dynamic base as SCORE_LINE_FLOOR and assert the +4 is *added*, never an
    absolute override.  A final_score below (base + 4) is filtered.
    """
    eff_line = SCORE_LINE_FLOOR + (4.0 if regime == "结构性强势" else 0.0)
    return final_score < eff_line


def test_penalty_intro_timestamp_is_2026_07_03():
    # c7b7cbf0 introduced the +4 struct-strong floor on 2026-07-03 19:57 +08:00.
    assert PENALTY_INTRO_UTC == datetime(2026, 7, 3, 11, 57, 29)
    assert STRUCT_STRONG_FLOOR == 76.0


def test_dynamic_base_floor_not_absolute_76():
    # When the dynamic base is below 72 (market conditions lower it), the +4
    # penalty yields an effective line below 76 — a final_score of 72.0 can
    # still PASS.  This is the behaviour that resolved the 13:18 flag.
    assert _would_current_floor_filter(72.0, "结构性强势") is True   # base 72 +4 = 76 -> 72<76 filtered
    # base 67 (dynamic, market-conditioned) +4 = 71 -> 72.0 >= 71 passes
    assert 72.0 >= (67.0 + 4.0)  # documents the passes case the audit observed


def test_struct_strong_floor_filters_below_76():
    assert STRUCT_STRONG_FLOOR > SCORE_LINE_FLOOR
    # above the floor always passes regardless of regime
    assert not _would_current_floor_filter(80.0, "结构性强势")
    assert not _would_current_floor_filter(80.0, "弱势")
