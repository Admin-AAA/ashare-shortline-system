"""ORM regression checks retained after minute-kbar schema changes moved to migrations."""
from __future__ import annotations

from sqlalchemy import UniqueConstraint


def test_minute_kbar_model_has_freq_field():
    """The ORM metadata keeps the explicit frequency discriminator."""
    from app.models.tables import MinuteKbar

    assert hasattr(MinuteKbar, "freq"), "MinuteKbar 缺少 freq 字段"
    unique_constraints = [
        item for item in MinuteKbar.__table_args__ if isinstance(item, UniqueConstraint)
    ]
    assert unique_constraints, "MinuteKbar 缺少 UniqueConstraint"
    assert {"code", "freq", "trade_time"} <= set(unique_constraints[0].columns.keys())


def test_minute_kbar_default_freq_value():
    """The ORM's default remains aligned with the frequency-aware model contract."""
    from app.models.tables import MinuteKbar

    column = MinuteKbar.__table__.c.get("freq")
    assert column is not None, "freq 列未在 __table__ 中注册"
    assert column.default is not None
    assert column.default.arg == "1min"
