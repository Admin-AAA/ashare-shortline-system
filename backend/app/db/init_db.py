"""Database initialization owned by the forward-only migration runner."""
from __future__ import annotations

from sqlalchemy.dialects.mysql import (
    LONGBLOB,
    LONGTEXT,
    MEDIUMBLOB,
    MEDIUMTEXT,
    TINYBLOB,
    TINYTEXT,
)
from sqlalchemy.ext.compiler import compiles

from app.db.alembic_runtime import upgrade_database
from app.db.session import engine


@compiles(LONGTEXT, "sqlite")
@compiles(MEDIUMTEXT, "sqlite")
@compiles(TINYTEXT, "sqlite")
def _compile_mysql_text_for_sqlite(type_, compiler, **kwargs) -> str:
    """Allow MySQL production text metadata to back unit-test SQLite databases."""
    del type_, compiler, kwargs
    return "TEXT"


@compiles(LONGBLOB, "sqlite")
@compiles(MEDIUMBLOB, "sqlite")
@compiles(TINYBLOB, "sqlite")
def _compile_mysql_blob_for_sqlite(type_, compiler, **kwargs) -> str:
    """Allow immutable binary artifacts in unit-test SQLite databases."""
    del type_, compiler, kwargs
    return "BLOB"


def init_db() -> None:
    """Bring the configured database to the Alembic head revision."""
    upgrade_database(engine)
