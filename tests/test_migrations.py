import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.storage.db import Database
from app.storage.migrations import BASELINE_STATEMENTS, MigrationError


class MigrationTests(unittest.TestCase):
    def test_new_database_creates_baseline_schema(self):
        with tempfile.TemporaryDirectory() as raw:
            db=Database(Path(raw)/"new.db")
            self.assertEqual(db.migrate(),[1])
            with db.connect() as connection:
                tables={row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
            self.assertTrue({"jobs","candidates","errors","snapshots","schema_migrations"} <= tables)

    def test_existing_database_is_baselined_without_data_loss(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"existing.db"
            connection=sqlite3.connect(path)
            for statement in BASELINE_STATEMENTS: connection.execute(statement)
            connection.execute("INSERT INTO jobs(mode,status) VALUES('mock','COMPLETED')")
            connection.commit(); connection.close()
            db=Database(path)
            self.assertEqual(db.migrate(),[1])
            with db.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],1)
                row=connection.execute("SELECT version,name,applied_at FROM schema_migrations").fetchone()
                self.assertEqual((row[0],row[1]),(1,"001_initial_baseline"))
                self.assertTrue(row[2])

    def test_second_run_does_not_reapply_migration(self):
        with tempfile.TemporaryDirectory() as raw:
            db=Database(Path(raw)/"repeat.db")
            self.assertEqual(db.migrate(),[1])
            with db.connect() as connection:
                first=connection.execute("SELECT applied_at FROM schema_migrations WHERE version=1").fetchone()[0]
            self.assertEqual(db.migrate(),[])
            with db.connect() as connection:
                rows=connection.execute("SELECT version,applied_at FROM schema_migrations").fetchall()
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0][1],first)

    def test_current_version_is_available(self):
        with tempfile.TemporaryDirectory() as raw:
            db=Database(Path(raw)/"version.db")
            self.assertEqual(db.schema_version(),0)
            db.migrate()
            self.assertEqual(db.schema_version(),1)

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
