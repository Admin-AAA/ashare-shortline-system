from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from app.db.session import Base
from app.models import tables  # noqa: F401 - register all ORM metadata
from app.services.auxiliary import snapshot_repository  # noqa: F401 - register Core metadata


config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        return

    # Standard Alembic CLI path. Application startup injects its transaction,
    # while operators running `alembic current/upgrade` use the configured engine.
    from app.db.session import engine

    with engine.connect() as cli_connection:
        context.configure(connection=cli_connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
