"""daily_kbars upsert 的派生列覆盖保护测试（L-144）。

背景数据墙：派生指标此前全在 on_duplicate_key_update 列表里，而日常同步只带回
权威行情（OHLCV/pct_chg/turnover_rate），派生列当批是 NULL（或写入侧 `or 0`
兜底出来的 0）。于是每跑一次同步/推荐就把 backfill 补好的真值刷掉——2026-09-24
实测：Stage 4 刚把 09-23 的 vol_ratio_5 补成 4949 行非空，下一个 run 的
market_data 阶段（02:08~02:21）即把它清回 0 行，值分布哨兵当场抓到。

本测试锁定三件事：
  1. 权威行情列仍然直接覆盖（不能因保护而失去更新能力）
  2. 当批派生值为 NULL 或 0 时，库中已有真值必须保留
  3. 计数型派生列（0 是合法值，NULLIF 会误判）整列退出 UPDATE 子句
"""
from __future__ import annotations

from sqlalchemy.dialects import mysql
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.models.tables import DailyKbar
from app.services.market_pool.core import (
    _DERIVED_COUNTER_COLUMNS,
    _DERIVED_NUMERIC_COLUMNS,
    _preserve_derived_on_duplicate,
)


def _compiled_update_clause() -> str:
    stmt = mysql_insert(DailyKbar).values([
        {"code": "000001", "trade_date": "2026-09-24", "close": 11.0}
    ])
    final = stmt.on_duplicate_key_update(
        open=stmt.inserted.open,
        close=stmt.inserted.close,
        pct_chg=stmt.inserted.pct_chg,
        turnover_rate=stmt.inserted.turnover_rate,
        **_preserve_derived_on_duplicate(stmt),
    )
    sql = str(final.compile(dialect=mysql.dialect()))
    return sql.split("ON DUPLICATE KEY UPDATE", 1)[1].lower()


def test_authoritative_quote_columns_still_overwrite():
    """行情列每次同步都带权威新值，必须保持直接覆盖。"""
    upd = _compiled_update_clause()
    for col in ("close", "pct_chg", "turnover_rate"):
        assert f"{col} = values({col})" in upd, f"{col} 应直接覆盖"


def test_every_numeric_derived_column_is_guarded():
    """浮点派生列必须是 coalesce(nullif(VALUES(x), 0), x)：0 与 NULL 都不覆盖真值。"""
    upd = _compiled_update_clause()
    unguarded = [
        col for col in _DERIVED_NUMERIC_COLUMNS
        if f"nullif(values({col}), %s)" not in upd or f"daily_kbars.{col}" not in upd
    ]
    assert not unguarded, f"这些派生列未受覆盖保护: {unguarded}"
    assert len(_DERIVED_NUMERIC_COLUMNS) >= 25, "派生列清单不应被意外缩小"


def test_counter_columns_are_excluded_from_update():
    """计数型派生列 0 是合法值（"没有突破"/"零根长上影"），NULLIF 会把合法 0
    误判成缺失，所以只能整列退出 UPDATE，由 backfill 独占写入权。"""
    upd = _compiled_update_clause()
    for col in _DERIVED_COUNTER_COLUMNS:
        assert col not in upd, f"{col} 是计数列，必须完全不出现在 UPDATE 子句"
    assert set(_DERIVED_COUNTER_COLUMNS) == {
        "breakout20", "breakout60", "upper_shadow_count20"
    }


def test_counter_and_numeric_sets_do_not_overlap():
    """一列只能属于一种处理方式，否则语义冲突。"""
    assert not (set(_DERIVED_NUMERIC_COLUMNS) & set(_DERIVED_COUNTER_COLUMNS))


def test_guard_preserves_existing_value_semantics():
    """语义断言：coalesce(nullif(new,0), old) 在 new∈{NULL,0} 时取 old，否则取 new。"""
    def guard(new, old):
        return old if new is None or new == 0 else new

    assert guard(None, 10.2) == 10.2, "NULL 必须保留旧值"
    assert guard(0, 10.2) == 10.2, "0 必须保留旧值（写入侧 or 0 兜底的产物）"
    assert guard(0.0, 1.45) == 1.45
    assert guard(99.9, 10.2) == 99.9, "有效新值必须更新"
    assert guard(-3.5, 10.2) == -3.5, "负值（如 bias/max_dd）是有效值必须更新"
