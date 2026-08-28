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
    Migration(3, "003_keepa_usage", (
        """CREATE TABLE IF NOT EXISTS keepa_usage(
            id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL,
            operation TEXT NOT NULL, asin TEXT,
            tokens_consumed INTEGER, tokens_left INTEGER,
            refill_rate INTEGER, refill_in INTEGER,
            token_flow_reduction REAL, processing_time_ms INTEGER,
            status TEXT NOT NULL, source TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_keepa_usage_observed_at ON keepa_usage(observed_at)",
        "CREATE INDEX IF NOT EXISTS idx_keepa_usage_operation ON keepa_usage(operation, observed_at)",
        """CREATE TABLE IF NOT EXISTS keepa_cache_hits(
            id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL,
            operation TEXT NOT NULL, asin TEXT, source TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_keepa_cache_hits_observed_at ON keepa_cache_hits(observed_at)",
    )),
    Migration(4, "004_opportunities", (
        """CREATE TABLE IF NOT EXISTS opportunities(
            opportunity_id TEXT PRIMARY KEY, identity_type TEXT NOT NULL,
            identity_value TEXT NOT NULL UNIQUE, asin TEXT, jan TEXT,
            product_name TEXT, manufacturer TEXT, observed_at TEXT NOT NULL,
            opportunity_score INTEGER NOT NULL, urgency_score INTEGER,
            confidence INTEGER, status TEXT NOT NULL, signal_count INTEGER NOT NULL,
            summary_json TEXT NOT NULL, reasons_json TEXT NOT NULL,
            risks_json TEXT NOT NULL, evidence_json TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_opportunities_score ON opportunities(status, opportunity_score DESC)",
        """CREATE TABLE IF NOT EXISTS opportunity_signals(
            id INTEGER PRIMARY KEY, opportunity_id TEXT NOT NULL,
            signal_type TEXT NOT NULL, source_engine TEXT NOT NULL,
            asin TEXT, jan TEXT, score INTEGER NOT NULL, candidate INTEGER NOT NULL,
            observed_at TEXT NOT NULL, reason TEXT NOT NULL,
            confidence INTEGER, quality TEXT, urgency_hint INTEGER,
            evidence_json TEXT NOT NULL,
            UNIQUE(opportunity_id, signal_type, source_engine, observed_at),
            FOREIGN KEY(opportunity_id) REFERENCES opportunities(opportunity_id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_opportunity_signals_opportunity ON opportunity_signals(opportunity_id)",
    )),
    Migration(5, "005_virtual_purchases", (
        """CREATE TABLE IF NOT EXISTS virtual_purchases(
            virtual_purchase_id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL,
            opportunity_observed_at TEXT NOT NULL, asin TEXT, jan TEXT, product_name TEXT,
            created_at TEXT NOT NULL, entry_price REAL NOT NULL,
            expected_sale_price REAL NOT NULL, expected_profit_yen REAL,
            expected_roi REAL, opportunity_score INTEGER NOT NULL,
            urgency_score INTEGER, confidence INTEGER, status TEXT NOT NULL,
            quantity INTEGER NOT NULL, snapshot_json TEXT NOT NULL,
            outcome_json TEXT NOT NULL, summary_json TEXT NOT NULL,
            UNIQUE(opportunity_id, opportunity_observed_at)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_virtual_purchases_status ON virtual_purchases(status, created_at)",
        """CREATE TABLE IF NOT EXISTS virtual_purchase_observations(
            id INTEGER PRIMARY KEY, virtual_purchase_id TEXT NOT NULL,
            observed_at TEXT NOT NULL, observed_price REAL, sales_rank INTEGER,
            new_offer_count INTEGER, amazon_owned INTEGER, data_quality TEXT,
            UNIQUE(virtual_purchase_id, observed_at),
            FOREIGN KEY(virtual_purchase_id) REFERENCES virtual_purchases(virtual_purchase_id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_virtual_purchase_observations_purchase ON virtual_purchase_observations(virtual_purchase_id, observed_at)",
    )),
    Migration(6, "006_profit_fee_model", (
        "ALTER TABLE virtual_purchases ADD COLUMN fee_source TEXT NOT NULL DEFAULT 'DEFAULT_ESTIMATE'",
        "ALTER TABLE virtual_purchases ADD COLUMN fee_model_version TEXT NOT NULL DEFAULT 'estimate_v1'",
        "ALTER TABLE virtual_purchases ADD COLUMN referral_fee REAL",
        "ALTER TABLE virtual_purchases ADD COLUMN fulfillment_fee REAL",
        "ALTER TABLE virtual_purchases ADD COLUMN total_fees REAL",
        "CREATE INDEX IF NOT EXISTS idx_virtual_purchases_fee_model ON virtual_purchases(fee_source, fee_model_version)",
    )),
    Migration(7, "007_seller_monitor", (
        """CREATE TABLE IF NOT EXISTS seller_monitors(
            seller_id TEXT PRIMARY KEY, seller_name TEXT, memo TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            last_checked_at TEXT, current_asin_count INTEGER NOT NULL DEFAULT 0,
            last_new_count INTEGER NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS seller_monitor_asins(
            seller_id TEXT NOT NULL, asin TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('BASELINE','NEW')),
            first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
            is_current INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(seller_id, asin),
            FOREIGN KEY(seller_id) REFERENCES seller_monitors(seller_id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_seller_monitor_asins_current ON seller_monitor_asins(seller_id,is_current)",
        """CREATE TABLE IF NOT EXISTS seller_monitor_detections(
            id INTEGER PRIMARY KEY, asin TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT 'seller_monitor',
            seller_id TEXT NOT NULL, detected_at TEXT NOT NULL,
            UNIQUE(seller_id, asin),
            FOREIGN KEY(seller_id) REFERENCES seller_monitors(seller_id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_seller_monitor_detections_at ON seller_monitor_detections(detected_at DESC)",
    )),
    Migration(8, "008_comparison_contract", (
        "ALTER TABLE opportunities ADD COLUMN source_type TEXT",
        "ALTER TABLE opportunities ADD COLUMN source_id TEXT",
        "ALTER TABLE opportunities ADD COLUMN strategy_version TEXT",
        "ALTER TABLE opportunity_signals ADD COLUMN source_type TEXT",
        "ALTER TABLE opportunity_signals ADD COLUMN source_id TEXT",
        "ALTER TABLE opportunity_signals ADD COLUMN strategy_version TEXT",
        "ALTER TABLE virtual_purchases ADD COLUMN source_type TEXT NOT NULL DEFAULT 'legacy'",
        "ALTER TABLE virtual_purchases ADD COLUMN source_id TEXT NOT NULL DEFAULT 'unknown'",
        "ALTER TABLE virtual_purchases ADD COLUMN strategy_version TEXT NOT NULL DEFAULT 'legacy'",
        "ALTER TABLE virtual_purchases ADD COLUMN evaluation_rule_version TEXT NOT NULL DEFAULT 'vp_eval_v1'",
        "ALTER TABLE virtual_purchases ADD COLUMN measurement_window_version TEXT NOT NULL DEFAULT 'vp_window_v1'",
        "CREATE INDEX IF NOT EXISTS idx_virtual_purchases_comparison ON virtual_purchases(source_type,source_id,strategy_version,evaluation_rule_version,measurement_window_version,fee_model_version)",
        """CREATE TABLE IF NOT EXISTS virtual_purchase_tracking_costs(
            id INTEGER PRIMARY KEY, virtual_purchase_id TEXT NOT NULL,
            observed_at TEXT NOT NULL, keepa_tokens INTEGER,
            api_calls INTEGER, ai_calls INTEGER, manual_review_count INTEGER,
            UNIQUE(virtual_purchase_id, observed_at),
            FOREIGN KEY(virtual_purchase_id) REFERENCES virtual_purchases(virtual_purchase_id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_virtual_purchase_tracking_costs_purchase ON virtual_purchase_tracking_costs(virtual_purchase_id,observed_at)",
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
