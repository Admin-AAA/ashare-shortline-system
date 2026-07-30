"""D 项 champion 基线查询必须套用 official sample scope（防漏口径虚高胜率）。

2026-07-30 发现：_print_low_score_line_pollution 的原生 SQL 漏掉 OFFICIAL_SAMPLE_SQL
（live_forward / completed / data_as_of<=td / source_snapshot_id / 生成日==交易日），
把 historical_replay 等非前瞻 formal outcome 计入 clean≥72 总体 → champion WR 由真值
~48.6% 被抬到 58.6%。本测试锁死查询常量必须含全套口径子句。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "self_check.py"
_spec = importlib.util.spec_from_file_location("self_check_scope", _SCRIPTS)
sc = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(sc)


def test_clean_pollution_sql_carries_official_scope():
    sql = sc.LIVE_FORMAL_CLEAN_POLLUTION_SQL
    # 全套 official sample 口径子句必须出现，缺一不可（小写化避免空格差异）
    required = [
        "live_forward",
        "u.status = 'completed'",
        "data_as_of IS NOT NULL",
        "data_as_of <= r.trade_date",
        "source_snapshot_id IS NOT NULL",
        "DATE_ADD(r.recommended_at",
        "r.recommendation_id LIKE 'rec_%'",
    ]
    low = sql.lower()
    for clause in required:
        assert clause.lower() in low, f"clean 污染查询漏掉口径子句: {clause}"


def test_clean_pollution_sql_matches_fetch_dedup_scope():
    """clean 污染查询的口径必须与 _fetch_dedup_live_formal 的 OFFICIAL_SAMPLE_SQL 一致。"""
    assert sc.OFFICIAL_SAMPLE_SQL in sc.LIVE_FORMAL_CLEAN_POLLUTION_SQL, (
        "clean 污染查询必须内联 OFFICIAL_SAMPLE_SQL，确保与前瞻 D 项同口径"
    )
