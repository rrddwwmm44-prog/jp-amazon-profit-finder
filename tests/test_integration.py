import os, tempfile, unittest
from unittest.mock import patch
from pathlib import Path
from app.config import Settings
from app.domain import ProductSignal
from app.storage.db import Database
from app.runner import run

class FailingProvider:
    name="broken"
    def fetch(self,cursor=None): raise RuntimeError("provider boom")

class HealthyProvider:
    name="healthy"
    def fetch(self,cursor=None): return [ProductSignal("healthy","ok","商品","メーカー")],"done"

class IntegrationTests(unittest.TestCase):
    def test_mock_pipeline(self):
        old=os.getcwd()
        with tempfile.TemporaryDirectory() as raw:
            try:
                tmp=Path(raw); os.chdir(tmp); db=Database(tmp/"test.db"); db.migrate(); settings=Settings(tmp/"test.db",500,.15,85,"INFO")
                job,items=run(db,settings,"mock")
                self.assertTrue(job); self.assertEqual(len(items),3); self.assertTrue((tmp/"data/exports/candidates.csv").exists())
                with db.connect() as c: self.assertEqual(c.execute("SELECT status FROM jobs WHERE id=?",(job,)).fetchone()[0],"COMPLETED")
            finally: os.chdir(old)

    def test_provider_failure_is_recorded_and_next_provider_continues(self):
        old=os.getcwd()
        with tempfile.TemporaryDirectory() as raw:
            try:
                tmp=Path(raw); os.chdir(tmp); db=Database(tmp/"test.db"); db.migrate(); settings=Settings(tmp/"test.db",500,.15,85,"INFO")
                with patch("app.runner.providers",return_value=[FailingProvider(),HealthyProvider()]):
                    job,items=run(db,settings,"live")
                self.assertEqual(len(items),1)
                with db.connect() as c:
                    self.assertEqual(c.execute("SELECT status FROM jobs WHERE id=?",(job,)).fetchone()[0],"COMPLETED")
                    error=c.execute("SELECT provider,error_class,message FROM errors WHERE job_id=?",(job,)).fetchone()
                    self.assertEqual(tuple(error),("broken","RuntimeError","provider boom"))
            finally: os.chdir(old)
