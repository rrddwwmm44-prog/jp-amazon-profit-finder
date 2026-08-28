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
            self.assertEqual(db.migrate(),[1,2,3,4,5,6,7,8])
            with db.connect() as connection:
                tables={row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
            self.assertTrue({"jobs","candidates","errors","snapshots","schema_migrations","keepa_cache","keepa_usage","keepa_cache_hits","opportunities","opportunity_signals","virtual_purchases","virtual_purchase_observations","seller_monitors","seller_monitor_asins","seller_monitor_detections","virtual_purchase_tracking_costs"} <= tables)

    def test_existing_database_is_baselined_without_data_loss(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"existing.db"
            connection=sqlite3.connect(path)
            for statement in BASELINE_STATEMENTS: connection.execute(statement)
            connection.execute("INSERT INTO jobs(mode,status) VALUES('mock','COMPLETED')")
            connection.commit(); connection.close()
            db=Database(path)
            self.assertEqual(db.migrate(),[1,2,3,4,5,6,7,8])
            with db.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],1)
                row=connection.execute("SELECT version,name,applied_at FROM schema_migrations").fetchone()
                self.assertEqual((row[0],row[1]),(1,"001_initial_baseline"))
                self.assertTrue(row[2])

    def test_second_run_does_not_reapply_migration(self):
        with tempfile.TemporaryDirectory() as raw:
            db=Database(Path(raw)/"repeat.db")
            self.assertEqual(db.migrate(),[1,2,3,4,5,6,7,8])
            with db.connect() as connection:
                first=connection.execute("SELECT applied_at FROM schema_migrations WHERE version=1").fetchone()[0]
            self.assertEqual(db.migrate(),[])
            with db.connect() as connection:
                rows=connection.execute("SELECT version,applied_at FROM schema_migrations").fetchall()
            self.assertEqual(len(rows),8)
            self.assertEqual(rows[0][1],first)

    def test_current_version_is_available(self):
        with tempfile.TemporaryDirectory() as raw:
            db=Database(Path(raw)/"version.db")
            self.assertEqual(db.schema_version(),0)
            db.migrate()
            self.assertEqual(db.schema_version(),8)

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
            self.assertEqual(db.migrate(),[2,3,4,5,6,7,8])
            with db.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],1)
                self.assertEqual(connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],8)

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
            self.assertEqual(db.migrate(),[3,4,5,6,7,8])
            self.assertEqual(db.schema_version(),8)
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
            self.assertEqual(db.migrate(),[4,5,6,7,8]); self.assertEqual(db.schema_version(),8)
            with db.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM keepa_usage").fetchone()[0],1)
                self.assertIsNotNone(connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='opportunities'").fetchone())

    def test_v4_database_upgrades_to_v5_without_data_loss(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"v4.db"; connection=sqlite3.connect(path)
            connection.execute(MIGRATION_TABLE_SQL)
            for migration in MIGRATIONS[:4]:
                for statement in migration.statements: connection.execute(statement)
                connection.execute("INSERT INTO schema_migrations(version,name) VALUES(?,?)",(migration.version,migration.name))
            connection.execute("INSERT INTO opportunities(opportunity_id,identity_type,identity_value,observed_at,opportunity_score,status,signal_count,summary_json,reasons_json,risks_json,evidence_json) VALUES('op1','asin','B0TEST0001','2026-01-01',90,'OPEN',1,'{}','[]','[]','[]')")
            connection.commit(); connection.close()
            db=Database(path); self.assertEqual(db.migrate(),[5,6,7,8]); self.assertEqual(db.schema_version(),8)
            with db.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0],1)

    def test_v5_database_baselines_existing_virtual_purchase_as_default_estimate(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"v5.db"; connection=sqlite3.connect(path)
            connection.execute(MIGRATION_TABLE_SQL)
            for migration in MIGRATIONS[:5]:
                for statement in migration.statements: connection.execute(statement)
                connection.execute("INSERT INTO schema_migrations(version,name) VALUES(?,?)",(migration.version,migration.name))
            connection.execute("""INSERT INTO virtual_purchases(
                virtual_purchase_id,opportunity_id,opportunity_observed_at,created_at,
                entry_price,expected_sale_price,opportunity_score,status,quantity,
                snapshot_json,outcome_json,summary_json
            ) VALUES('vp1','op1','2026-01-01','2026-01-01',3000,5500,90,'OPEN',1,'{}','{}','{}')""")
            connection.commit(); connection.close()
            db=Database(path); self.assertEqual(db.migrate(),[6,7,8]); self.assertEqual(db.schema_version(),8)
            with db.connect() as connection:
                row=connection.execute("SELECT fee_source,fee_model_version,referral_fee FROM virtual_purchases").fetchone()
            self.assertEqual((row[0],row[1],row[2]),("DEFAULT_ESTIMATE","estimate_v1",None))

    def test_v7_virtual_purchase_is_safely_baselined_for_comparison(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"v7.db"; connection=sqlite3.connect(path)
            connection.execute(MIGRATION_TABLE_SQL)
            for migration in MIGRATIONS[:7]:
                for statement in migration.statements: connection.execute(statement)
                connection.execute("INSERT INTO schema_migrations(version,name) VALUES(?,?)",(migration.version,migration.name))
            connection.execute("""INSERT INTO virtual_purchases(
                virtual_purchase_id,opportunity_id,opportunity_observed_at,created_at,
                entry_price,expected_sale_price,opportunity_score,status,quantity,
                snapshot_json,outcome_json,summary_json
            ) VALUES('vp1','op1','2026-01-01','2026-01-01',3000,5500,90,'OPEN',1,'{}','{}','{}')""")
            connection.commit(); connection.close()
            db=Database(path); self.assertEqual(db.migrate(),[8])
            with db.connect() as connection:
                row=connection.execute("""SELECT source_type,source_id,strategy_version,
                    evaluation_rule_version,measurement_window_version
                    FROM virtual_purchases""").fetchone()
            self.assertEqual(tuple(row),("legacy","unknown","legacy","vp_eval_v1","vp_window_v1"))

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
