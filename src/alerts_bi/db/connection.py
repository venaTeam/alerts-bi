"""SQL Server connection management.

The store is the non-negotiable part of this design: Elasticsearch retains three months
and the pre-project period is expiring at a rate of one day per day, so a run that cannot
persist has not done its job. Reports are therefore rendered only from committed rows.

Driver note: ``pymssql`` uses ``pyformat`` placeholders (``%(name)s``) where the superseded
JavaScript driver used ``@name``. That is the one mechanical change the port required; no
stored value, column, constraint or transaction boundary changes with it. A literal ``%``
inside SQL text must be doubled when parameters are supplied, which is why the DDL - which
takes no parameters - is executed separately from parameterized statements.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import pymssql

from alerts_bi.config import SqlConfig
from alerts_bi.logging_setup import log, redact_error

__all__ = ["Database", "connect", "quote_identifier"]


def quote_identifier(name: str) -> str:
    """Quote a SQL Server identifier, rejecting anything that is not a plain name.

    Database names reach this from configuration and the reset path below is destructive,
    so the safe set is deliberately narrow.
    """
    if not name or len(name) > 128:
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    if not (name[0].isalpha() or name[0] == "_"):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    if not all(ch.isalnum() or ch == "_" for ch in name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return f"[{name}]"


class Database:
    """A connection plus the few helpers the pipeline needs."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)

    def execute_many(self, sql: str, rows: Sequence[dict[str, Any]]) -> None:
        if not rows:
            return
        with self.connection.cursor() as cursor:
            cursor.executemany(sql, list(rows))

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.connection.cursor(as_dict=True) as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall())

    def query_one(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def close(self) -> None:
        self.connection.close()

    @contextmanager
    def transaction(self) -> Iterator[Database]:
        """Run a block inside a transaction, rolling back on any error.

        Persistence is all-or-nothing per run: a half-written run would be
        indistinguishable from a complete one when the report is rendered from SQL.
        """
        try:
            yield self
            self.commit()
        except BaseException:
            try:
                self.rollback()
            except Exception as rollback_error:
                log.error("sql.rollback_failed", error=redact_error(rollback_error))
            raise


@contextmanager
def connect(
    config: SqlConfig, database: str | None = None, autocommit: bool = False
) -> Iterator[Database]:
    """Open a connection to one database, closing it on exit."""
    connection = pymssql.connect(
        server=config.host,
        port=str(config.port),
        user=config.user,
        password=config.password,
        database=database if database is not None else config.database,
        timeout=int(config.request_timeout_ms / 1000),
        login_timeout=int(config.request_timeout_ms / 1000),
        autocommit=autocommit,
    )
    db = Database(connection)
    try:
        yield db
    finally:
        db.close()
