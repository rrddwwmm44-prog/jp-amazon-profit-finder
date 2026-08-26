import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.storage.db import Database
from app.storage.migrations import BASELINE_STATEMENTS, MIGRATIONS, MIGRATION_TABLE_SQL, MigrationError


class MigrationTests(unittest.TestCase):
    def test_new_database_creates_baseline_schema(self):
        with tempfile.TemporaryDirectory() as raw:
            db=Database(Path(raw)/"new.db")
            self.assertEqual(db.migrate(),[1,2,3,4])
            with db.connect() as connection:
                tables={row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
            self.assertTrue({"jobs","candidates","errors","snapshots","schema_migrations","keepa_cache","keepa_usage","keepa_cache_hits","opportunities","opportunity_signals"} <= tables)

    def test_existing_database_is_baselined_without_data_loss(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"existing.db"
            connection=sqlite3.connect(path)
            for statement in BASELINE_STATEMENTS: connection.execute(statement)
            connection.execute("INSERT INTO jobs(mode,status) VALUES('mock','COMPLETED')")
            connection.commit(); connection.close()
            db=Database(path)
            self.assertEqual(db.migrate(),[1,2,3,4])
            with db.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],1)
                row=connection.execute("SELECT version,name,applied_at FROM schema_migrations").fetchone()
                self.assertEqual((row[0],row[1]),(1,"001_initial_baseline"))
                self.assertTrue(row[2])

    def test_second_run_does_not_reapply_migration(self):
        with tempfile.TemporaryDirectory() as raw:
            db=Database(Path(raw)/"repeat.db")
            self.assertEqual(db.migrate(),[1,2,3,4])
            with db.connect() as connection:
                first=connection.execute("SELECT applied_at FROM schema_migrations WHERE version=1").fetchone()[0]
            self.assertEqual(db.migrate(),[])
            with db.connect() as connection:
                rows=connection.execute("SELECT version,applied_at FROM schema_migrations").fetchall()
            self.assertEqual(len(rows),4)
            self.assertEqual(rows[0][1],first)

    def test_current_version_is_available(self):
        with tempfile.TemporaryDirectory() as raw:
            db=Database(Path(raw)/"version.db")
            self.assertEqual(db.schema_version(),0)
            db.migrate()
            self.assertEqual(db.schema_version(),4)

    def test_v1_database_upgrades_to_latest_without_data_loss(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"v1.db"
            connection=sqlite3.connect(path)
            for statement in BASELINE_STATEMENTS: connection.execute(statement)
            connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE,applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            connection.execute("INSERT INTO schema_migrations(version,name) VALUES(1,'001_initial_baseline')")
            connection.execute("INSERT INTO jobs(mode,status) VALUES('mock','COMPLETED')")
            connection.commit(); connection.close()
            db=Database(path)
            self.assertEqual(db.migrate(),[2,3,4])
            with db.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],1)
                self.assertEqual(connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],4)

    def test_v2_database_upgrades_to_v3_without_data_loss(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"v2.db"
            connection=sqlite3.connect(path)
            connection.execute(MIGRATION_TABLE_SQL)
            for migration in MIGRATIONS[:2]:
                for statement in migration.statements: connection.execute(statement)
                connection.execute("INSERT INTO schema_migrations(version,name) VALUES(?,?)",(migration.version,migration.name))
            connection.execute("INSERT INTO keepa_cache(asin,marketplace,observed_at,result_json) VALUES('B012345678','amazon.co.jp','2026-01-01T00:00:00+00:00','{}')")
            connection.commit(); connection.close()
            db=Database(path)
            self.assertEqual(db.schema_version(),2)
            self.assertEqual(db.migrate(),[3,4])
            self.assertEqual(db.schema_version(),4)
            with db.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM keepa_cache").fetchone()[0],1)

    def test_v3_database_upgrades_to_v4_without_data_loss(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"v3.db"
            connection=sqlite3.connect(path)
            connection.execute(MIGRATION_TABLE_SQL)
            for migration in MIGRATIONS[:3]:
                for statement in migration.statements: connection.execute(statement)
                connection.execute("INSERT INTO schema_migrations(version,name) VALUES(?,?)",(migration.version,migration.name))
            connection.execute("INSERT INTO keepa_usage(observed_at,operation,status,source) VALUES('2026-01-01T00:00:00+00:00','product','success','test')")
            connection.commit(); connection.close()
            db=Database(path)
            self.assertEqual(db.migrate(),[4]); self.assertEqual(db.schema_version(),4)
            with db.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM keepa_usage").fetchone()[0],1)
                self.assertIsNotNone(connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='opportunities'").fetchone())

    def test_failed_baseline_is_not_recorded(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"broken.db"
            connection=sqlite3.connect(path)
            connection.execute("CREATE TABLE jobs(id INTEGER PRIMARY KEY)")
            connection.commit(); connection.close()
            db=Database(path)
            with self.assertRaises(MigrationError) as raised:
                db.migrate()
            self.assertEqual((raised.exception.version,raised.exception.name),(1,"001_initial_baseline"))
            self.assertIn("missing required columns",str(raised.exception))
            with db.connect() as connection:
                migration_table=connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
                ).fetchone()
            self.assertIsNone(migration_table)
