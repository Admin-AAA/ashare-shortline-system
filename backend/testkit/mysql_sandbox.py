"""Safety attestation for golden tests that intentionally use real MySQL.

Golden parity tests are offline and normally require no database.  If a later
adapter integration test needs real MySQL, it must either bind its Session to an
already-open rollback transaction or use a clearly dedicated test schema.  This
module fails closed for ordinary business-schema sessions.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session


DEFAULT_DEDICATED_SCHEMA_PREFIX = "ashare_golden_test_"


class UnsafeMySQLTestEnvironment(RuntimeError):
    """The supplied Session is not isolated from the business schema."""


class MySQLSandboxMode(StrEnum):
    ROLLBACK_TRANSACTION = "rollback_transaction"
    DEDICATED_SCHEMA = "dedicated_schema"


@dataclass(frozen=True)
class MySQLSandboxAttestation:
    mode: MySQLSandboxMode
    schema_name: str | None
    external_transaction: bool


def validate_mysql_sandbox(
    *,
    dialect_name: str,
    schema_name: str | None,
    external_transaction: bool,
    dedicated_schema_prefix: str = DEFAULT_DEDICATED_SCHEMA_PREFIX,
) -> MySQLSandboxAttestation:
    """Validate explicit isolation facts without opening a connection."""

    if dialect_name.lower() != "mysql":
        raise UnsafeMySQLTestEnvironment("real-MySQL golden checks require the mysql dialect")
    if external_transaction:
        return MySQLSandboxAttestation(
            mode=MySQLSandboxMode.ROLLBACK_TRANSACTION,
            schema_name=schema_name,
            external_transaction=True,
        )
    prefix = dedicated_schema_prefix.strip()
    if not prefix:
        raise ValueError("dedicated_schema_prefix must be non-empty")
    if schema_name and schema_name.startswith(prefix):
        return MySQLSandboxAttestation(
            mode=MySQLSandboxMode.DEDICATED_SCHEMA,
            schema_name=schema_name,
            external_transaction=False,
        )
    raise UnsafeMySQLTestEnvironment(
        "real-MySQL golden checks require an external rollback transaction "
        f"or a schema prefixed with {prefix!r}"
    )


def attest_real_mysql_sandbox(
    session: Session,
    *,
    dedicated_schema_prefix: str = DEFAULT_DEDICATED_SCHEMA_PREFIX,
) -> MySQLSandboxAttestation:
    """Inspect a Session and prove it is rollback-bound or schema-isolated.

    A normal ``Session(engine)`` transaction is not sufficient: application code
    can commit it.  The rollback mode is accepted only when ``Session.get_bind``
    is an externally owned ``Connection`` whose transaction was started before
    the Session was created, matching the repository's ``db`` pytest fixture.
    """

    if not isinstance(session, Session):
        raise TypeError("session must be a sqlalchemy.orm.Session")
    bind = session.get_bind()
    dialect_name = str(bind.dialect.name)
    external_transaction = isinstance(bind, Connection) and bind.in_transaction()
    schema_name: str | None = None
    if not external_transaction:
        connection = session.connection()
        value = connection.exec_driver_sql("SELECT DATABASE()").scalar_one_or_none()
        schema_name = str(value) if value is not None else None
    return validate_mysql_sandbox(
        dialect_name=dialect_name,
        schema_name=schema_name,
        external_transaction=external_transaction,
        dedicated_schema_prefix=dedicated_schema_prefix,
    )


__all__ = [
    "DEFAULT_DEDICATED_SCHEMA_PREFIX",
    "MySQLSandboxAttestation",
    "MySQLSandboxMode",
    "UnsafeMySQLTestEnvironment",
    "attest_real_mysql_sandbox",
    "validate_mysql_sandbox",
]
