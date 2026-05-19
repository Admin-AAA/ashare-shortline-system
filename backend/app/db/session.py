import logging
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


settings = get_settings()

if not settings.database_url.startswith("mysql"):
    raise RuntimeError(
        "ASHARE_DATABASE_URL 必须使用 MySQL 连接串。"
    )

_connect_args = {"charset": "utf8mb4", "connect_timeout": 10, "read_timeout": 900, "write_timeout": 900}
_engine_kwargs = {
    "pool_size": 20,
    "max_overflow": 15,
    "pool_timeout": 30,
    "pool_pre_ping": True,
    "pool_recycle": 600,  # Agent 审查期间 DB 空闲可达 250s+，600s 回收避免 MySQL 服务端 kill 连接
}

engine = create_engine(settings.database_url, connect_args=_connect_args, **_engine_kwargs)


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
