from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.cli import main
from app.config import Settings
from app.providers.keepa import KeepaProduct, KeepaResult, KeepaTokenMetadata
from app.providers.keepa import KeepaSellerResult
from app.seller_monitor.pipeline import SellerDetectionPipeline
from app.seller_monitor.service import SellerMonitorService
from app.services.keepa_history import HistoryQuality, NormalizedKeepaHistory
from app.storage.db import Database


NOW=datetime(2026,8,28,tzinfo=timezone.utc)
SELLER_A="A12345678901"
SELLER_B="A12345678902"
ASIN="B000000001"


def settings(path: Path) -> Settings:
    return Settings(path,500,0.15,85,"INFO")


def keepa_result(asin=ASIN, *, cache=False, candidate=True):
    price=3000 if candidate else 5400
    history=NormalizedKeepaHistory(
        asin,"Fixture",NOW.isoformat(),price,price,5500,5500,5500.0,5500.0,
        12000,12000,12000,12000,4,5,6,7,price,None,False,HistoryQuality.COMPLETE,
    )
    product=KeepaProduct(asin,5,"amazon.co.jp","Fixture",price,None,12000,4,NOW.isoformat())
    tokens=None if cache else KeepaTokenMetadata(98,2,5,1000)
    return KeepaResult(product,tokens,cache,history)


class ProductProvider:
    def __init__(self, result=None): self.result=result or keepa_result(); self.calls=[]
    def get_product(self, asin): self.calls.append(asin); return self.result


class StorefrontProvider:
    def __init__(self, observations): self.observations=iter(observations)
    def get_seller_storefront(self,seller_id):
        return KeepaSellerResult(seller_id,"Fixture",next(self.observations),None)


class SellerMonitorPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.db=Database(Path(self.temp.name)/"pipeline.db"); self.db.migrate()
        self.settings=settings(self.db.path)

    def add_detection(self, seller_id=SELLER_A, asin=ASIN, enabled=True, detected_at=None):
        at=(detected_at or NOW).isoformat()
        with self.db.connect() as c:
            c.execute("""INSERT INTO seller_monitors(seller_id,enabled,created_at,updated_at,last_checked_at)
                VALUES(?,?,?,?,?)""",(seller_id,int(enabled),at,at,at))
            c.execute("""INSERT INTO seller_monitor_detections(asin,source_type,seller_id,detected_at)
                VALUES(?,'seller_monitor',?,?)""",(asin,seller_id,at))

    def pipeline(self,provider=None,budget=None):
        return SellerDetectionPipeline(
            self.settings,self.db,provider or ProductProvider(),
            budget or (lambda db,now=None:{"status":"HEALTHY","tokens_left":100}),
        )

    def test_new_detection_reaches_signal_opportunity_and_virtual_purchase(self):
        self.add_detection()
        report=self.pipeline().run(now=NOW)
        self.assertEqual((report.detection_count,report.opportunities_saved,report.virtual_purchases_created),(1,1,1))
        with self.db.connect() as c:
            signals=[tuple(row) for row in c.execute("""SELECT signal_type,source_type,source_id,strategy_version
                FROM opportunity_signals ORDER BY id""")]
            status=c.execute("SELECT processing_status FROM seller_monitor_detections").fetchone()[0]
        self.assertIn(("seller_monitor_new","seller_monitor",SELLER_A,"seller_monitor_v1"),signals)
        self.assertIn("amazon_arbitrage",{row[0] for row in signals})
        self.assertEqual(status,"PROCESSED")
        purchase=self.db.load_virtual_purchases()[0]
        contract=purchase.entry_snapshot.comparison_contract
        self.assertEqual((contract.source_type,contract.source_id,contract.strategy_version),
                         ("seller_monitor",SELLER_A,"seller_monitor_v1"))
        self.assertEqual((contract.evaluation_rule_version,contract.measurement_window_version,contract.fee_model_version),
                         ("vp_eval_v1","vp_window_v1","estimate_v1"))

    def test_baseline_creates_no_detection_or_signal(self):
        service=SellerMonitorService(self.db,StorefrontProvider(((ASIN,),)),lambda db,now:{"status":"HEALTHY"})
        service.add(SELLER_A); service.check(SELLER_A,now=NOW)
        report=self.pipeline(ProductProvider()).run(now=NOW)
        self.assertEqual((report.detection_count,report.signals_created,report.virtual_purchases_created),(0,0,0))

    def test_disabled_monitor_detection_is_not_processed(self):
        self.add_detection(enabled=False); provider=ProductProvider()
        report=self.pipeline(provider).run(now=NOW)
        self.assertEqual((report.detection_count,len(provider.calls)),(0,0))

    def test_duplicate_retry_does_not_duplicate_signal_or_virtual_purchase(self):
        self.add_detection(); pipeline=self.pipeline(); pipeline.run(now=NOW)
        with self.db.connect() as c:
            first_signals=c.execute("SELECT COUNT(*) FROM opportunity_signals").fetchone()[0]
            c.execute("UPDATE seller_monitor_detections SET processing_status='PENDING'")
        report=pipeline.run(now=NOW)
        with self.db.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM opportunity_signals").fetchone()[0],first_signals)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM virtual_purchases").fetchone()[0],1)
        self.assertEqual((report.duplicates,report.virtual_purchases_created),(1,0))

    def test_multiple_sellers_keep_all_source_provenance(self):
        self.add_detection(SELLER_A)
        self.add_detection(SELLER_B,detected_at=NOW.replace(hour=1))
        self.pipeline().run(now=NOW)
        with self.db.connect() as c:
            sellers={row[0] for row in c.execute("""SELECT source_id FROM opportunity_signals
                WHERE source_type='seller_monitor'""")}
        self.assertEqual(sellers,{SELLER_A,SELLER_B})
        self.assertEqual(len(self.db.load_virtual_purchases()),1)

    def test_ineligible_refinement_creates_opportunity_but_not_virtual_purchase(self):
        self.add_detection(); report=self.pipeline(ProductProvider(keepa_result(candidate=False))).run(now=NOW)
        self.assertEqual((report.opportunities_saved,report.virtual_purchases_created,report.skipped),(1,0,1))

    def test_exhausted_budget_makes_no_provider_call_or_partial_update(self):
        self.add_detection(); provider=ProductProvider()
        report=self.pipeline(provider,lambda db,now=None:{"status":"EXHAUSTED","tokens_left":0}).run(now=NOW)
        self.assertEqual((report.planned_count,len(provider.calls)),(0,0))
        with self.db.connect() as c:
            self.assertEqual(c.execute("SELECT processing_status FROM seller_monitor_detections").fetchone()[0],"PENDING")

    def test_tracking_cost_counts_refinement_token_once_and_unknown_costs_are_null(self):
        self.add_detection(); pipeline=self.pipeline(); pipeline.run(now=NOW)
        with self.db.connect() as c:
            first=tuple(c.execute("SELECT keepa_tokens,api_calls,ai_calls,manual_review_count FROM virtual_purchase_tracking_costs").fetchone())
            c.execute("UPDATE seller_monitor_detections SET processing_status='PENDING'")
        pipeline.run(now=NOW)
        with self.db.connect() as c:
            count=c.execute("SELECT COUNT(*) FROM virtual_purchase_tracking_costs").fetchone()[0]
        self.assertEqual(first,(2,None,None,None)); self.assertEqual(count,1)

    def test_cli_dry_run_reports_pending_without_keepa(self):
        self.add_detection()
        with patch("app.config._load_dotenv"),patch.dict(os.environ,{"APP_DB_PATH":str(self.db.path)},clear=True),redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["seller-process","--dry-run"]),0)
        report=json.loads(output.getvalue())
        self.assertEqual((report["detection_count"],report["planned_count"]),(1,1))


if __name__ == "__main__": unittest.main()
