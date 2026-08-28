from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.cli import main
from app.config import Settings
from app.providers.keepa import KeepaProduct, KeepaResult, KeepaSellerResult, KeepaTokenMetadata
from app.seller_monitor.daily import SellerMonitorDailyService
from app.services.job_lock import JobLock
from app.services.keepa_history import HistoryQuality, NormalizedKeepaHistory
from app.storage.db import Database
from scripts import register_seller_monitor_daily_task as registration
from scripts import run_seller_monitor_daily as wrapper


NOW=datetime(2026,8,29,tzinfo=timezone.utc)
SELLERS=("A12345678901","A12345678902","A12345678903")
ASIN="B000000001"


def settings(path: Path) -> Settings: return Settings(path,500,0.15,85,"INFO",job_lock_dir=path.parent/"locks")
def budget(status="HEALTHY",left=100): return lambda db,now=None:{"status":status,"tokens_left":left}


class DailyProvider:
    def __init__(self,db: Database,storefronts=None,fail=()):
        self.db=db; self.storefronts=storefronts or {}; self.fail=set(fail); self.seller_calls=[]; self.product_calls=[]
    def get_seller_storefront(self,seller_id):
        self.seller_calls.append(seller_id)
        if seller_id in self.fail: raise RuntimeError("fixture seller failure")
        tokens=KeepaTokenMetadata(90,10,5,1000)
        self.db.record_keepa_usage(datetime.now(timezone.utc).isoformat(),"seller_storefront",seller_id,tokens,"success")
        return KeepaSellerResult(seller_id,"Fixture",tuple(self.storefronts.get(seller_id,())),tokens)
    def get_product(self,asin):
        self.product_calls.append(asin); tokens=KeepaTokenMetadata(88,2,5,1000)
        self.db.record_keepa_usage(datetime.now(timezone.utc).isoformat(),"product",asin,tokens,"success")
        history=NormalizedKeepaHistory(
            asin,"Fixture",NOW.isoformat(),3000,3000,5500,5500,5500.0,5500.0,
            12000,12000,12000,12000,4,5,6,7,3000,None,False,HistoryQuality.COMPLETE,
        )
        return KeepaResult(KeepaProduct(asin,5,"amazon.co.jp","Fixture",3000,None,12000,4,NOW.isoformat()),tokens,False,history)


class SellerMonitorDailyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.db=Database(Path(self.temp.name)/"daily.db"); self.db.migrate(); self.settings=settings(self.db.path)

    def add_seller(self,seller_id,*,baseline=None,enabled=True):
        at=NOW.isoformat()
        with self.db.connect() as c:
            c.execute("""INSERT INTO seller_monitors(seller_id,enabled,created_at,updated_at,last_checked_at)
                VALUES(?,?,?,?,?)""",(seller_id,int(enabled),at,at,at if baseline is not None else None))
            for asin in baseline or ():
                c.execute("""INSERT INTO seller_monitor_asins(seller_id,asin,status,first_seen_at,last_seen_at)
                    VALUES(?,?,'BASELINE',?,?)""",(seller_id,asin,at,at))

    def daily(self,provider,status="HEALTHY",left=100):
        return SellerMonitorDailyService(self.settings,self.db,provider,budget(status,left))

    def test_zero_sellers_is_safe(self):
        report=self.daily(DailyProvider(self.db)).run(now=NOW)
        self.assertEqual((report.active_sellers,report.storefront_checked,report.errors),(0,0,0))

    def test_first_run_is_baseline_only(self):
        self.add_seller(SELLERS[0]); provider=DailyProvider(self.db,{SELLERS[0]:(ASIN,)})
        report=self.daily(provider).run(now=NOW)
        self.assertEqual((report.baseline_count,report.new_detection_count,report.signals_created,report.virtual_purchases_created),(1,0,0,0))
        with self.db.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM seller_monitor_detections").fetchone()[0],0)

    def test_new_detection_runs_complete_pipeline_and_summary(self):
        self.add_seller(SELLERS[0],baseline=("B000000000",))
        provider=DailyProvider(self.db,{SELLERS[0]:("B000000000",ASIN)})
        report=self.daily(provider).run(now=NOW)
        self.assertEqual((report.active_sellers,report.storefront_checked,report.new_detection_count),(1,1,1))
        self.assertEqual((report.processed_detection_count,report.opportunities_saved,report.virtual_purchases_created),(1,1,1))
        self.assertEqual((report.keepa_requests,report.tokens_consumed),(2,12))
        self.assertTrue(report.started_at); self.assertTrue(report.completed_at)

    def test_seller_failure_is_isolated(self):
        for seller in SELLERS: self.add_seller(seller)
        provider=DailyProvider(self.db,{SELLERS[0]:(),SELLERS[2]:()},fail=(SELLERS[1],))
        report=self.daily(provider).run(now=NOW)
        self.assertEqual((report.storefront_checked,report.errors),(2,1))
        self.assertEqual(provider.seller_calls,list(SELLERS))
        with self.db.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM errors WHERE provider LIKE 'seller:%'").fetchone()[0],1)

    def test_budget_exhausted_calls_no_keepa(self):
        self.add_seller(SELLERS[0]); provider=DailyProvider(self.db)
        report=self.daily(provider,"EXHAUSTED",0).run(now=NOW)
        self.assertEqual((report.storefront_planned,report.keepa_requests,len(provider.seller_calls)),(0,0,0))

    def test_daily_caps_sellers_at_five(self):
        sellers=tuple(f"A123456789{i:02d}" for i in range(1,7))
        for seller in sellers: self.add_seller(seller)
        provider=DailyProvider(self.db,{seller:() for seller in sellers})
        report=self.daily(provider).run(now=NOW)
        self.assertEqual((report.active_sellers,report.storefront_planned,report.storefront_checked),(6,5,5))

    def test_budget_is_rechecked_between_sellers(self):
        self.add_seller(SELLERS[0]); self.add_seller(SELLERS[1])
        provider=DailyProvider(self.db,{SELLERS[0]:(),SELLERS[1]:()}); calls=0
        def changing_budget(db,now=None):
            nonlocal calls; calls+=1
            return {"status":"HEALTHY","tokens_left":100} if calls <= 2 else {"status":"EXHAUSTED","tokens_left":0}
        report=SellerMonitorDailyService(self.settings,self.db,provider,changing_budget).run(now=NOW)
        self.assertEqual((report.storefront_planned,report.storefront_checked,len(provider.seller_calls)),(2,1,1))

    def test_duplicate_daily_run_is_idempotent(self):
        self.add_seller(SELLERS[0],baseline=("B000000000",))
        provider=DailyProvider(self.db,{SELLERS[0]:("B000000000",ASIN)})
        first=self.daily(provider).run(now=NOW); second=self.daily(provider).run(now=NOW)
        self.assertEqual((first.virtual_purchases_created,second.virtual_purchases_created),(1,0))
        with self.db.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM virtual_purchases").fetchone()[0],1)

    def test_cli_dry_run_is_noop_and_common_lock_blocks_competition(self):
        self.add_seller(SELLERS[0]); holder=JobLock(self.settings.job_lock_dir,"seller-monitor"); holder.acquire(); self.addCleanup(holder.release)
        with patch("app.config._load_dotenv"),patch.dict(os.environ,{"APP_DB_PATH":str(self.db.path),"APP_LOCK_DIR":str(self.settings.job_lock_dir)},clear=True),redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["seller-monitor-daily","--dry-run"]),0)
        self.assertEqual(json.loads(output.getvalue())["status"],"already_running")

    def test_wrapper_writes_retained_log(self):
        with tempfile.TemporaryDirectory() as raw,patch("scripts.run_seller_monitor_daily.subprocess.run",return_value=subprocess.CompletedProcess([],0,'{"active_sellers":0}','')):
            self.assertEqual(wrapper.run_daily(dry_run=True,log_directory=Path(raw)),0)
            text=next(Path(raw).glob("seller-monitor-daily-*.log")).read_text(encoding="utf-8")
        self.assertIn("exit_code=0",text); self.assertIn("active_sellers",text)

    def test_scheduler_plan_is_disabled_and_configurable(self):
        plan=registration.build_plan(schedule_time="05:15")
        self.assertFalse(plan.enabled); self.assertEqual(plan.schedule,"Daily 05:15")
        enabled=registration.build_plan(schedule_time="05:30",enabled=True)
        self.assertTrue(enabled.enabled); self.assertEqual(enabled.schedule,"Daily 05:30")


if __name__ == "__main__": unittest.main()
