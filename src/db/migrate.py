"""Migration runner.

Production uses these same files with an externally supplied connection string; tests do
not substitute SQLite or another engine, because a constraint that only exists in one
dialect is a constraint that was never tested.

The migration files are unchanged from the JavaScript implementation, and the checksum is
computed the same way (line endings normalized to LF before hashing), so a database
migrated by either implementation is accepted by the other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.config import SqlConfig
from src.db.connection import Database, connect, quote_identifier
from src.hashing import sha256_text
from src.logging_setup import log

__all__ = [
    "MIGRATIONS_DIR",
    "Migration",
    "applied_migrations",
    "load_migrations",
    "migrate",
    "migrate_database",
    "reset_test_database",
]

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

#: ``GO`` is a batch separator understood by sqlcmd, not by the driver.
_GO = re.compile(r"^\s*GO\s*$", re.MULTILINE | re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    filename: str
    sql_text: str
    checksum: str


def load_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Read migrations from disk in version order."""
    migrations: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        sql_text = path.read_bytes().decode("utf-8")
        migrations.append(
            Migration(
                version=path.stem,
                filename=path.name,
                sql_text=sql_text,
                checksum=sha256_text(sql_text.replace("\r\n", "\n")),
            )
        )
    return migrations


def _ensure_migrations_table(db: Database) -> None:
    db.execute(
        """
        IF OBJECT_ID('schema_migrations', 'U') IS NULL
        CREATE TABLE schema_migrations (
          version    NVARCHAR(128) NOT NULL CONSTRAINT pk_schema_migrations PRIMARY KEY,
          checksum   NVARCHAR(64)  NOT NULL,
          applied_at DATETIME2(3)  NOT NULL
        );
        """
    )
    db.commit()


def applied_migrations(db: Database) -> dict[str, str]:
    """Return ``{version: checksum}`` for migrations already applied."""
    _ensure_migrations_table(db)
    rows = db.query("SELECT version, checksum FROM schema_migrations")
    return {str(row["version"]): str(row["checksum"]) for row in rows}


def migrate(db: Database, directory: Path = MIGRATIONS_DIR) -> tuple[list[str], list[str]]:
    """Apply every migration not yet recorded.

    A migration whose file changed after being applied is a hard error: silently
    re-applying or ignoring it would leave the database in a state no version describes.

    Returns ``(newly_applied, already_applied)``.
    """
    applied = applied_migrations(db)
    newly_applied: list[str] = []
    already_applied: list[str] = []

    for migration in load_migrations(directory):
        existing = applied.get(migration.version)
        if existing is not None:
            if existing != migration.checksum:
                raise RuntimeError(
                    f"migration {migration.filename} was already applied but its contents "
                    "have changed (add a new migration instead of editing an applied one)"
                )
            already_applied.append(migration.version)
            continue

        # Each migration runs in its own transaction: a failure leaves the previous ones
        # applied and recorded, so a rerun resumes rather than starting over.
        try:
            for batch in _GO.split(migration.sql_text):
                if batch.strip():
                    db.execute(batch)
            db.execute(
                "INSERT INTO schema_migrations (version, checksum, applied_at) "
                "VALUES (:version, :checksum, :applied_at)",
                {
                    "version": migration.version,
                    "checksum": migration.checksum,
                    "applied_at": datetime.now(UTC).replace(tzinfo=None),
                },
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            raise RuntimeError(f"migration {migration.filename} failed: {exc}") from exc

        newly_applied.append(migration.version)
        log.info("db.migration_applied", version=migration.version)

    return newly_applied, already_applied


def migrate_database(config: SqlConfig, database: str) -> tuple[list[str], list[str]]:
    """Create the target database if it does not exist, then apply migrations to it."""
    quoted = quote_identifier(database)
    with connect(config, "master", autocommit=True) as master:
        exists = master.query_one(
            "SELECT 1 AS present FROM sys.databases WHERE name = :name", {"name": database}
        )
        if exists is None:
            master.execute(f"CREATE DATABASE {quoted}")
            log.info("db.database_created", database=database)

    with connect(config, database) as db:
        return migrate(db)


def reset_test_database(config: SqlConfig, database: str) -> None:
    """Drop and recreate ONLY the explicitly named disposable test database.

    Guarded on purpose: this is the one destructive operation in the codebase. It refuses
    any database whose name is not the configured test database, so a mistyped environment
    variable cannot take out ``alerts_bi_dev`` or a production store.
    """
    if database != config.test_database:
        raise ValueError(
            f"refusing to reset {database!r}: only the configured test database "
            f"({config.test_database!r}) may be recreated"
        )
    if "test" not in database.lower():
        raise ValueError(
            f'refusing to reset {database!r}: a disposable test database name must contain "test"'
        )

    quoted = quote_identifier(database)
    with connect(config, "master", autocommit=True) as master:
        # Single-user mode rolls back open sessions so the drop cannot hang behind a
        # connection left over from an interrupted test run.
        master.execute(
            f"""
            IF EXISTS (SELECT 1 FROM sys.databases WHERE name = N'{database}')
            BEGIN
              ALTER DATABASE {quoted} SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
              DROP DATABASE {quoted};
            END
            """
        )
        master.execute(f"CREATE DATABASE {quoted}")
        log.info("db.test_database_recreated", database=database)

    with connect(config, database) as db:
        migrate(db)
