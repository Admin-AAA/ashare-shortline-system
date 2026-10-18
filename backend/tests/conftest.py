import sys
import os
from pathlib import Path

# 测试绝不能连生产 Prefect（:4200）。在任何 prefect 导入前把 API 指向不可达地址，
# 任何漏网的 get_client()/run_deployment 都会立即连接失败而不是在生产建 flow run。
# 历史事故：test_recover_interrupted_runs_marks_active_runs 把事务内假 queued run
# 重新派发到生产 Prefect，worker 看不到未提交行 → 每天数条 recommendation-run FAILED。
os.environ["PREFECT_API_URL"] = "http://127.0.0.1:9/api"

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
        pytest.skip("需要可用 MySQL 连接；当前回归运行在无数据库模式")
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


class ProductionPrefectDispatchBlocked(RuntimeError):
    """测试中试图向 Prefect 投递 deployment run。"""


@pytest.fixture(autouse=True)
def _block_prefect_dispatch(request, monkeypatch):
    """所有测试默认禁止 Prefect 派发：拦截 run_deployment，记录并在 teardown 失败。

    业务代码常把派发异常吞掉只记日志（如 recover_interrupted_runs），所以仅抛异常不够，
    teardown 时检查尝试记录并让测试失败。需要验证派发的测试自行 monkeypatch
    ``app.orchestration.api.run_deployment``（会覆盖本拦截），或标记
    ``@pytest.mark.allow_prefect_dispatch_attempt``（仍拦截，但不报失败）。
    """
    attempts: list[dict] = []

    def _blocked_run_deployment(*args, **kwargs):
        attempts.append({"args": args, "kwargs": kwargs})
        raise ProductionPrefectDispatchBlocked(
            f"tests must not dispatch Prefect deployments: name={kwargs.get('name')!r}"
        )

    try:
        import app.orchestration.api as orchestration_api
    except Exception:  # pragma: no cover - prefect 不可用时没有派发风险
        orchestration_api = None
    if orchestration_api is not None:
        monkeypatch.setattr(orchestration_api, "run_deployment", _blocked_run_deployment)
    yield attempts
    if attempts and request.node.get_closest_marker("allow_prefect_dispatch_attempt") is None:
        pytest.fail(
            f"test attempted {len(attempts)} Prefect deployment dispatch(es): "
            + ", ".join(str(a["kwargs"].get("name")) for a in attempts)
        )
