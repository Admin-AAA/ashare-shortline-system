import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import get_settings

_logging_initialized = False


def setup_logging() -> None:
    global _logging_initialized
    if _logging_initialized:
        return
    _logging_initialized = True

    settings = get_settings()

    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(settings.log_level.upper())

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [pid %(process)d] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler — INFO and above, with rotation（API 进程与 Prefect flow 子进程都写这里，pid 区分）
    file_handler = RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Access log — INFO and above, separate file for API timing
    access_handler = RotatingFileHandler(
        log_dir / "access.log",
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    access_handler.setLevel(logging.INFO)
    access_handler.setFormatter(formatter)
    access_logger = logging.getLogger("app.access")
    access_logger.setLevel(logging.INFO)
    access_logger.propagate = False
    access_logger.addHandler(access_handler)

    # Console handler — INFO and above
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 第三方库的 INFO 很吵（每个 HTTP 请求一行），只保留 WARNING+
    for noisy in ("httpx", "httpcore", "urllib3", "sqlalchemy.engine", "asyncio", "prefect.events",
                  "uvicorn.access", "watchfiles", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Capture unhandled exceptions
    def _unhandled_exception_hook(exc_type, exc_value, exc_tb):
        root_logger.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = _unhandled_exception_hook
