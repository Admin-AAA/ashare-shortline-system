import sys
import os
from pathlib import Path

# 测试绝不能连生产 Prefect（:4200）。在任何 prefect 导入前把 API 指向不可达地址，
# 任何漏网的 get_client()/run_deployment 都会立即连接失败而不是在生产建 flow run。
# 历史事故：test_recover_interrupted_runs_marks_active_runs 把事务内假 queued run
# 重新派发到生产 Prefect，worker 看不到未提交行 → 每天数条 recommendation-run FAILED。
os.environ["PREFECT_API_URL"] = "http://127.0.0.1:9/api"
# 事务内造数的测试需要现算数据池统计，不能读生产落盘的预计算缓存。
os.environ.setdefault("ASHARE_POOL_STATS_CACHE", "off")

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import close_all_sessions, sessionmaker


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import Settings


PRODUCTION_DB_NAME = "ashare_workbench"
TEST_DB_NAME = "ashare_test"


def _resolve_test_db_url() -> str:
    """测试库 URL：默认把生产 URL 的库名换成 ashare_test，且拒绝连接生产库。

    显式指定用 ASHARE_TEST_DATABASE_URL。生产库 ashare_workbench 一律拒绝
    （历史上测试直连生产库造成过生产 Prefect 假失败、绕过回滚的写入）。
    建库：CREATE DATABASE ashare_test; ASHARE_DATABASE_URL=<test url> alembic upgrade head
    """
    from sqlalchemy.engine import make_url

    raw = (os.environ.get("ASHARE_TEST_DATABASE_URL") or "").strip()
    if not raw:
        base = (os.environ.get("ASHARE_DATABASE_URL") or Settings().database_url).strip()
        if not base.startswith("mysql"):
            return base
        raw = make_url(base).set(database=TEST_DB_NAME).render_as_string(hide_password=False)
    if raw.startswith("mysql") and make_url(raw).database == PRODUCTION_DB_NAME:
        raise RuntimeError(
            f"tests refuse to connect to the production database {PRODUCTION_DB_NAME!r}; "
            f"use {TEST_DB_NAME!r} (ASHARE_TEST_DATABASE_URL)"
        )
    return raw


TEST_DB_URL = _resolve_test_db_url()
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


def _test_db_has_market_data() -> bool:
    if not _HAS_TEST_DB:
        return False
    try:
        with engine.connect() as conn:
            return conn.execute(text("SELECT 1 FROM daily_kbars LIMIT 1")).first() is not None
    except SQLAlchemyError:
        return False


@pytest.fixture(autouse=True)
def _skip_without_market_data(request):
    """``@pytest.mark.requires_market_data``：依赖真实行情数据的集成测试。

    测试库 ashare_test 只有 schema、没有行情；这类测试在测试库无行情时跳过，
    绝不回退到生产库。需要时把只读样本导入 ashare_test 即可自动启用。
    """
    if request.node.get_closest_marker("requires_market_data") and not _test_db_has_market_data():
        pytest.skip("ashare_test 无行情数据（requires_market_data）")
