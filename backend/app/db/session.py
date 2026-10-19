import logging
from collections.abc import Generator
from datetime import datetime

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


settings = get_settings()

PRODUCTION_DB_NAME = "ashare_workbench"


def _under_pytest() -> bool:
    """pytest 进程本身或其派生子进程（conftest 设 ASHARE_UNDER_PYTEST=1 并被继承）。"""
    import os
    import sys

    return "pytest" in sys.modules or bool(os.environ.get("PYTEST_CURRENT_TEST")) or os.environ.get("ASHARE_UNDER_PYTEST") == "1"


def _refuse_production_db_under_pytest(url: str) -> None:
    """第二道闸（第一道在 tests/conftest.py）：任何测试路径都不得连生产库。

    覆盖 tests/conftest.py 管不到的入口：`pytest app/**/tests`（不在 tests/ 下、不加载该 conftest）、
    测试里起的子进程（继承 ASHARE_UNDER_PYTEST）。2026-07-10 diag 探针在生产库留下
    977483/967711 及 07-09 的 17 行假 K 线，2026-10-19 清理。
    """
    if not url.startswith("mysql") or not _under_pytest():
        return
    from sqlalchemy.engine import make_url

    if make_url(url).database == PRODUCTION_DB_NAME:
        raise RuntimeError(
            f"refuse to connect to production database {PRODUCTION_DB_NAME!r} under pytest; "
            "use ashare_test (ASHARE_TEST_DATABASE_URL)"
        )


_refuse_production_db_under_pytest(settings.database_url)

# 支持 SQLite 本地开发（后端自动检测）
_is_sqlite = settings.database_url.startswith("sqlite")

if _is_sqlite:
    import sqlite3

    # Python 3.12 deprecated sqlite3's implicit datetime adapter. Register the
    # explicit ISO adapter used by our existing naive-UTC DATETIME contract.
    sqlite3.register_adapter(datetime, lambda value: value.isoformat(" "))
    _connect_args = {"check_same_thread": False}
    _engine_kwargs = {"poolclass": None, "connect_args": _connect_args}
    engine = create_engine(settings.database_url, **_engine_kwargs)
else:
    _connect_args = {"charset": "utf8mb4", "connect_timeout": 10, "read_timeout": 300, "write_timeout": 120}
    _engine_kwargs = {
        "pool_size": 20,
        "max_overflow": 15,
        "pool_timeout": 30,
        "pool_pre_ping": True,
        "pool_recycle": 3600,  # 1小时回收，避免长查询期间连接过期
    }
    engine = create_engine(settings.database_url, connect_args=_connect_args, **_engine_kwargs)


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


# 防御：任何路径（含外部脚本/工具连入）写入中文前强制 utf8mb4。
# 背景（2026-08-02 诊断）：MySQL 服务器级 character_set_client/connection 为
# latin1，曾导致 7 月 REPLAY run 的 run_name/filter_audit_json 中文被写坏
# （"REPLAY-全量重放" 落库为 "REPLAY-????"，读库出现非法字节）。
@event.listens_for(engine, "connect")
def _force_utf8mb4(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("SET NAMES utf8mb4")
        cursor.close()
    except Exception:  # pragma: no cover - 非 MySQL 后端（如 SQLite）忽略  # noqa: BLE001, S110
        pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
