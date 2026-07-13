import sys
import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import close_all_sessions, sessionmaker


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import Settings


TEST_DB_URL = (
    os.environ.get("ASHARE_DATABASE_URL")
    or Settings().database_url
).strip()
_HAS_TEST_DB = bool(TEST_DB_URL and TEST_DB_URL.startswith("mysql"))
_MYSQL_LOCK_NAME = "ashare_pytest_db_lock"
_MYSQL_LOCK_WAIT_SECONDS = int(os.environ.get("ASHARE_PYTEST_DB_LOCK_SECONDS", "5"))

if _HAS_TEST_DB:
    os.environ["ASHARE_DATABASE_URL"] = TEST_DB_URL
    from app.db.session import engine


@pytest.fixture(scope="session", autouse=True)
def _exclusive_test_db_lock():
    if not _HAS_TEST_DB:
        yield
        return
    lock_connection = engine.connect()
    try:
        acquired = lock_connection.scalar(
            text("SELECT GET_LOCK(:lock_name, :wait_seconds)"),
            {"lock_name": _MYSQL_LOCK_NAME, "wait_seconds": _MYSQL_LOCK_WAIT_SECONDS},
        )
    except SQLAlchemyError as exc:
        lock_connection.close()
        raise RuntimeError(
            "无法获取 MySQL 测试锁；生产库测试需要串行执行。"
            f"lock={_MYSQL_LOCK_NAME}"
        ) from exc
    if acquired != 1:
        holder = lock_connection.scalar(
            text("SELECT IS_USED_LOCK(:lock_name)"),
            {"lock_name": _MYSQL_LOCK_NAME},
        )
        lock_connection.close()
        raise RuntimeError(
            "无法获取 MySQL 测试锁，请确认没有其他 pytest 进程正在使用同一生产库。"
            f"lock={_MYSQL_LOCK_NAME}, holder_connection_id={holder or 'unknown'}"
        )
    try:
        yield
    finally:
        try:
            try:
                lock_connection.execute(
                    text("SELECT RELEASE_LOCK(:lock_name)"),
                    {"lock_name": _MYSQL_LOCK_NAME},
                )
            except Exception:
                # MySQL 会在连接断开时自动释放 GET_LOCK；生产库长连接空闲超时不应反向污染测试结果。
                pass
        finally:
            lock_connection.close()


@pytest.fixture(scope="session", autouse=True)
def _init_test_schema(_exclusive_test_db_lock):
    close_all_sessions()
    yield
    close_all_sessions()
    if _HAS_TEST_DB:
        engine.dispose()


@pytest.fixture
def production_mysql(_init_test_schema):
    if not _HAS_TEST_DB:
        raise RuntimeError("production_mysql 需要可用 MySQL 连接；demo 阶段默认直接使用生产库;所有MySQL Redis都连接不上，如果需要测试MySQL，你可以考虑mock或者直接放弃测试")
    yield


@pytest.fixture
def db(production_mysql):
    connection = engine.connect()
    transaction = connection.begin()
    TestingSessionLocal = sessionmaker(
        bind=connection,
        autoflush=False,
        autocommit=False,
        join_transaction_mode="create_savepoint",
    )
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()
