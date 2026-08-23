import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.cli import main
from app.providers.keepa import KeepaTokenMetadata
from app.services.keepa_budget import build_keepa_budget
from app.storage.db import Database


class KeepaBudgetTests(unittest.TestCase):
    def make_db(self,raw):
        db=Database(Path(raw)/"budget.db"); db.migrate(); return db

    def record(self,db,at,consumed,left=100,refill=5,status="success"):
        tokens=KeepaTokenMetadata(left,consumed,refill,1000,0.0,10)
        db.record_keepa_usage(at.isoformat(),"product","B012345678",tokens,status)

    def test_windows_capacity_required_shortage_and_recommendation(self):
        with tempfile.TemporaryDirectory() as raw:
            db=self.make_db(raw)
            now=datetime(2026,1,1,1,0,tzinfo=timezone.utc)
            self.record(db,datetime(2026,1,1,0,0,tzinfo=timezone.utc),4,100,2)
            self.record(db,datetime(2026,1,1,0,2,tzinfo=timezone.utc),2,98,2)
            db.record_keepa_cache_hit(datetime(2026,1,1,0,3,tzinfo=timezone.utc).isoformat(),"product","B012345678")
            report=build_keepa_budget(db,now)
            window=report["windows"]["last_24h"]
            self.assertEqual((window["requests"],window["tokens_consumed"],window["cache_hits"]),(2,6,1))
            self.assertAlmostEqual(window["cache_hit_rate"],1/3,places=4)
            self.assertEqual(report["current_capacity_tokens_per_min"],2)
            self.assertEqual(report["required"]["average_required_tokens_per_min"],3)
            self.assertEqual(report["required"]["peak_required_tokens_per_min"],4)
            self.assertEqual(report["shortage"],{"average":1,"peak":2})
            self.assertEqual(report["recommended"],{"minimum_tokens_per_min":5,"comfortable_tokens_per_min":10})
            self.assertEqual(report["status"],"CRITICAL")

    def test_insufficient_data_does_not_claim_prediction(self):
        with tempfile.TemporaryDirectory() as raw:
            db=self.make_db(raw); now=datetime(2026,1,1,1,0,tzinfo=timezone.utc)
            self.record(db,now,1)
            report=build_keepa_budget(db,now)
            self.assertEqual(report["required"]["status"],"insufficient_data")
            self.assertIsNone(report["required"]["average_required_tokens_per_min"])
            self.assertIsNone(report["recommended"]["minimum_tokens_per_min"])

    def test_external_consumption_is_estimated_only_inside_expiry_window(self):
        with tempfile.TemporaryDirectory() as raw:
            db=self.make_db(raw); now=datetime(2026,1,1,1,0,tzinfo=timezone.utc)
            self.record(db,datetime(2026,1,1,0,50,tzinfo=timezone.utc),1,100,5)
            self.record(db,now,1,140,5)
            self.assertEqual(build_keepa_budget(db,now)["estimated_external_consumption"],9)
        with tempfile.TemporaryDirectory() as raw:
            db=self.make_db(raw)
            self.record(db,datetime(2026,1,1,0,0,tzinfo=timezone.utc),1,100,5)
            self.record(db,datetime(2026,1,1,2,0,tzinfo=timezone.utc),1,100,5)
            self.assertIsNone(build_keepa_budget(db,datetime(2026,1,1,2,0,tzinfo=timezone.utc))["estimated_external_consumption"])

    def test_budget_cli_does_not_call_keepa(self):
        with tempfile.TemporaryDirectory() as raw:
            old=os.getcwd()
            try:
                os.chdir(raw)
                with patch.dict(os.environ,{"APP_DB_PATH":str(Path(raw)/"cli.db")},clear=True), patch("app.providers.keepa.KeepaHttpClient.get_product",side_effect=AssertionError("API called")), redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(main(["keepa-budget"]),0)
                self.assertIn('"status": "UNKNOWN"',output.getvalue())
            finally:
                os.chdir(old)


if __name__ == "__main__":
    unittest.main()
