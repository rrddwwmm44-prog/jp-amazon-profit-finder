from __future__ import annotations

from dataclasses import dataclass
import sqlite3


MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations(
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


BASELINE_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS jobs(
        id INTEGER PRIMARY KEY, mode TEXT, status TEXT, cursor TEXT,
        started_at TEXT DEFAULT CURRENT_TIMESTAMP, completed_at TEXT, error TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS candidates(
        id INTEGER PRIMARY KEY, job_id INTEGER, identity_key TEXT, observed_at TEXT,
        score INTEGER, confidence INTEGER, verification TEXT, reason TEXT,
        product_name TEXT, manufacturer TEXT, jan TEXT, asin TEXT,
        amazon_price REAL, source_price REAL, profit_yen REAL, margin REAL, roi REAL,
        seller_count INTEGER, sales_rank INTEGER, status TEXT, evidence_json TEXT,
        UNIQUE(job_id, identity_key)
    )""",
    """CREATE TABLE IF NOT EXISTS errors(
        id INTEGER PRIMARY KEY, job_id INTEGER, provider TEXT, error_class TEXT,
        message TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS snapshots(
        id INTEGER PRIMARY KEY, provider TEXT, identity_key TEXT, payload_json TEXT,
        observed_at TEXT, UNIQUE(provider, identity_key, observed_at)
    )""",
)


MIGRATIONS = (
    Migration(1, "001_initial_baseline", BASELINE_STATEMENTS),
    Migration(2, "002_keepa_response_cache", (
        """CREATE TABLE IF NOT EXISTS keepa_cache(
            asin TEXT NOT NULL, marketplace TEXT NOT NULL,
            observed_at TEXT NOT NULL, result_json TEXT NOT NULL,
            PRIMARY KEY(asin, marketplace)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_keepa_cache_observed_at ON keepa_cache(observed_at)",
    )),
)


REQUIRED_BASELINE_COLUMNS = {
    "jobs": {"id", "mode", "status", "cursor", "started_at", "completed_at", "error"},
    "candidates": {
        "id", "job_id", "identity_key", "observed_at", "score", "confidence",
        "verification", "reason", "product_name", "manufacturer", "jan", "asin",
        "amazon_price", "source_price", "profit_yen", "margin", "roi",
        "seller_count", "sales_rank", "status", "evidence_json",
    },
    "errors": {"id", "job_id", "provider", "error_class", "message", "created_at"},
    "snapshots": {"id", "provider", "identity_key", "payload_json", "observed_at"},
}


class MigrationError(RuntimeError):
    def __init__(self, version: int, name: str, cause: Exception):
        self.version = version
        self.name = name
        super().__init__(f"migration {version} ({name}) failed: {cause}")


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _validate_baseline(connection: sqlite3.Connection) -> None:
    for table, required in REQUIRED_BASELINE_COLUMNS.items():
        actual = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
        missing = required - actual
        if missing:
            raise sqlite3.DatabaseError(
                f"existing table {table} is missing required columns: {', '.join(sorted(missing))}"
            )


def current_version(connection: sqlite3.Connection) -> int:
    if not _table_exists(connection, "schema_migrations"):
        return 0
    row = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
    return int(row[0])


def apply_migrations(connection: sqlite3.Connection) -> list[int]:
    """Apply only pending migrations and return their versions.

    Existing v1 databases are safely baselined because migration 1 uses only
    additive, idempotent DDL and validates the existing columns before its
    version is recorded.
    """
    applied_now: list[int] = []
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(MIGRATION_TABLE_SQL)
        applied = {
            int(row[0]) for row in connection.execute("SELECT version FROM schema_migrations")
        }
        for migration in MIGRATIONS:
            if migration.version in applied:
                continue
            try:
                for statement in migration.statements:
                    connection.execute(statement)
                if migration.version == 1:
                    _validate_baseline(connection)
                connection.execute(
                    "INSERT INTO schema_migrations(version,name) VALUES(?,?)",
                    (migration.version, migration.name),
                )
                applied_now.append(migration.version)
            except Exception as exc:
                raise MigrationError(migration.version, migration.name, exc) from exc
        connection.commit()
        return applied_now
    except Exception:
        connection.rollback()
        raise
