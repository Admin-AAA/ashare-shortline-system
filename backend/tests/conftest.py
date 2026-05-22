import sys
import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import close_all_sessions


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

TEST_DB_URL = (os.environ.get("ASHARE_TEST_DATABASE_URL") or os.environ.get("ASHARE_DATABASE_URL") or "").strip()
if not TEST_DB_URL:
    raise RuntimeError("运行后端测试前必须设置 ASHARE_TEST_DATABASE_URL，并指向独立的 MySQL 测试库。")
if not TEST_DB_URL.startswith("mysql"):
    raise RuntimeError("后端测试仅支持 MySQL；ASHARE_TEST_DATABASE_URL 必须是 MySQL 连接串。")

os.environ["ASHARE_DATABASE_URL"] = TEST_DB_URL

from app.db.init_db import init_db
from app.db.session import Base, engine


def _truncate_all_tables() -> None:
    close_all_sessions()
    engine.dispose()
    with engine.connect() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        try:
            for table in reversed(Base.metadata.sorted_tables):
                connection.execute(text(f"TRUNCATE TABLE `{table.name}`"))
        finally:
            try:
                connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
                connection.commit()
            except Exception:
                connection.rollback()
                raise


@pytest.fixture(scope="session", autouse=True)
def _exclusive_test_db_lock():
    lock_connection = engine.connect()
    acquired = lock_connection.scalar(text("SELECT GET_LOCK('ashare_pytest_db_lock', 120)"))
    if acquired != 1:
        lock_connection.close()
        raise RuntimeError("无法获取 MySQL 测试库独占锁，请确认没有其他 pytest 进程在使用同一测试库。")
    try:
        yield
    finally:
        try:
            lock_connection.execute(text("SELECT RELEASE_LOCK('ashare_pytest_db_lock')"))
        finally:
            lock_connection.close()


@pytest.fixture(scope="session", autouse=True)
def _init_test_schema(_exclusive_test_db_lock):
    close_all_sessions()
    engine.dispose()
    Base.metadata.drop_all(bind=engine, checkfirst=True)
    init_db()
    yield
    close_all_sessions()
    engine.dispose()


@pytest.fixture
def clean_test_db(_init_test_schema):
    _truncate_all_tables()
    yield
    _truncate_all_tables()
