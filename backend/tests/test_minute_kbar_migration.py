"""
测试 MinuteKbar freq 字段迁移逻辑。

覆盖场景：
1. 模型定义包含 freq 字段，默认值 "1min"
2. UniqueConstraint 包含 freq
3. 迁移逻辑能正确添加 freq 列（SQLite 模拟）
"""
from __future__ import annotations

import pytest
from sqlalchemy import Column, Float, Integer, MetaData, String, Table, UniqueConstraint, create_engine, inspect, text


def test_minute_kbar_model_has_freq_field():
    """验证 MinuteKbar 模型有 freq 字段且默认 '1min'。"""
    from app.models.tables import MinuteKbar

    # 确认字段存在
    assert hasattr(MinuteKbar, "freq"), "MinuteKbar 缺少 freq 字段"

    # 确认 UniqueConstraint 包含 freq
    table_args = MinuteKbar.__table_args__
    uq = next((arg for arg in table_args if isinstance(arg, UniqueConstraint)), None)
    assert uq is not None, "MinuteKbar 缺少 UniqueConstraint"
    assert "freq" in uq.columns, "UniqueConstraint 应包含 freq"
    assert "code" in uq.columns, "UniqueConstraint 应包含 code"
    assert "trade_time" in uq.columns, "UniqueConstraint 应包含 trade_time"


def test_minute_kbar_default_freq_value():
    """验证 MinuteKbar freq 默认值是 '1min'。"""
    from app.models.tables import MinuteKbar

    col = MinuteKbar.__table__.c.get("freq")
    assert col is not None, "freq 列未在 __table__ 中注册"
    default = col.default.arg if col.default else None
    assert default == "1min", f"freq 默认值应为 '1min', 实际为 {default}"


def test_migrate_minute_kbars_adds_freq_column():
    """模拟手动迁移：旧表（无 freq 列）→ 迁移后应有 freq 列。"""
    from app.db.init_db import _migrate_minute_kbars

    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()

    # 创建旧版 minute_kbars（无 freq 列）
    Table(
        "minute_kbars",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("code", String(6)),
        Column("trade_date", String(10)),
        Column("trade_time", String(19)),
        Column("open", Float, default=0),
        Column("high", Float, default=0),
        Column("low", Float, default=0),
        Column("close", Float, default=0),
        Column("volume", Float, default=0),
        Column("amount", Float, default=0),
        UniqueConstraint("code", "trade_time", name="uq_minute_kbar_code_time"),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        inspector = inspect(connection)
        # 确认迁移前无 freq 列
        cols = {c["name"] for c in inspector.get_columns("minute_kbars")}
        assert "freq" not in cols, "模拟旧表不应有 freq 列"

        # 执行迁移
        _migrate_minute_kbars(connection, inspector)

        # 重新 inspect 获取最新的列信息（迁移函数内部会刷新 inspector）
        from sqlalchemy import inspect as _fi
        fresh = _fi(connection)
        cols = {c["name"] for c in fresh.get_columns("minute_kbars")}
        assert "freq" in cols, "迁移后应包含 freq 列"

        # 确认可以插入带 freq 的数据
        connection.execute(
            text("INSERT INTO minute_kbars (code, trade_date, trade_time, freq, open, close, volume, amount) "
                 "VALUES (:code, :date, :time, :freq, :open, :close, :vol, :amt)"),
            {"code": "000001", "date": "2026-05-26", "time": "2026-05-26 09:30:00",
             "freq": "1min", "open": 10.0, "close": 10.1, "vol": 1000, "amt": 10000},
        )

        # 查询验证
        row = connection.execute(
            text("SELECT code, freq, trade_time FROM minute_kbars WHERE code = '000001'")
        ).fetchone()
        assert row is not None
        assert row[0] == "000001"
        assert row[1] == "1min"
        assert row[2] == "2026-05-26 09:30:00"


def test_migrate_minute_kbars_idempotent():
    """迁移逻辑应是幂等的——重复迁移不会出错。"""
    from app.db.init_db import _migrate_minute_kbars

    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()

    Table(
        "minute_kbars",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("code", String(6)),
        Column("trade_date", String(10)),
        Column("trade_time", String(19)),
        Column("open", Float, default=0),
        Column("high", Float, default=0),
        Column("low", Float, default=0),
        Column("close", Float, default=0),
        Column("volume", Float, default=0),
        Column("amount", Float, default=0),
        UniqueConstraint("code", "trade_time", name="uq_minute_kbar_code_time"),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        inspector = inspect(connection)

        # 第一次迁移
        _migrate_minute_kbars(connection, inspector)

        # 重新加载 inspector
        inspector = inspect(connection)

        # 第二次迁移——不应报错
        _migrate_minute_kbars(connection, inspector)

        # 确认 freq 列仍然存在且只有一个
        cols = {c["name"] for c in inspector.get_columns("minute_kbars")}
        assert "freq" in cols, "第二次迁移后 freq 列应仍然存在"
