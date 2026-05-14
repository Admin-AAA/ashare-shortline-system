import logging
import threading
import time
from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


settings = get_settings()

_tunnel: object | None = None  # sshtunnel.SSHTunnelForwarder | None
_tunnel_lock = threading.Lock()


def _ensure_ssh_tunnel() -> None:
    """本地开发时自动建立 SSH 隧道连接远程 MySQL。"""
    global _tunnel
    if not settings.ssh_host or not settings.ssh_password:
        return
    if _tunnel is not None:
        return
    with _tunnel_lock:
        if _tunnel is not None:
            return
        try:
            from sshtunnel import SSHTunnelForwarder

            _tunnel = SSHTunnelForwarder(
                (settings.ssh_host, settings.ssh_port),
                ssh_username=settings.ssh_user,
                ssh_password=settings.ssh_password,
                remote_bind_address=("127.0.0.1", 3306),
                local_bind_address=("127.0.0.1", 13306),
                set_keepalive=30,
            )
            _tunnel.start()
            logger.info("SSH tunnel established to %s:%d", settings.ssh_host, settings.ssh_port)
        except Exception as exc:
            logger.warning("SSH tunnel failed (%s), falling back to direct connection", exc)
            _tunnel = None


_connect_args = {}
_engine_kwargs = {}

if settings.database_url.startswith("mysql"):
    _connect_args = {"charset": "utf8mb4", "connect_timeout": 10}
    _engine_kwargs = {
        "pool_size": 20,
        "max_overflow": 15,
        "pool_timeout": 30,
        "pool_pre_ping": True,
        "pool_recycle": 3600,
    }
elif settings.database_url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False, "timeout": 30}
    _engine_kwargs = {"pool_size": 20, "max_overflow": 15, "pool_timeout": 30, "pool_pre_ping": True}

_ensure_ssh_tunnel()

engine = create_engine(settings.database_url, connect_args=_connect_args, **_engine_kwargs)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
