import logging
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


settings = get_settings()

# 支持 SQLite 本地开发（后端自动检测）
_is_sqlite = settings.database_url.startswith("sqlite")

if _is_sqlite:
    _connect_args = {"check_same_thread": False}
    _engine_kwargs = {"poolclass": None, "connect_args": _connect_args}
    engine = create_engine(settings.database_url, **_engine_kwargs)
else:
    _connect_args = {"charset": "utf8mb4", "connect_timeout": 10, "read_timeout": 900, "write_timeout": 900}
    _engine_kwargs = {
        "pool_size": 20,
        "max_overflow": 15,
        "pool_timeout": 30,
        "pool_pre_ping": True,
        "pool_recycle": 600,
    }
    engine = create_engine(settings.database_url, connect_args=_connect_args, **_engine_kwargs)


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
