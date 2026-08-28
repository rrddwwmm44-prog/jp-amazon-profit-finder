from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.opportunities.aggregator import OpportunityAggregator
from app.opportunities.models import Signal
from app.providers.keepa import KeepaProduct, KeepaResult, KeepaTokenMetadata
from app.storage.db import Database
from app.virtual_purchases.service import VirtualPurchaseService
from app.virtual_purchases.tracking import VirtualPurchaseTrackingService


NOW=datetime(2026,5,1,tzinfo=timezone.utc)


def settings(path: Path) -> Settings:
    return replace(Settings(path,500,0.15,85,"INFO"),virtual_purchase_track_interval_hours=0)


def opportunity(*, sourced: bool = True):
    signal=Signal(
        "amazon_arbitrage","seller_monitor","B0CONTRACT",None,NOW.isoformat(),90,True,"new asin",
        {"purchase_price":3000,"expected_sale_price":5500},confidence=80,
        source_type="seller_monitor" if sourced else None,
        source_id="SELLER00001" if sourced else None,
        strategy_version="seller_monitor_v1" if sourced else None,
    )
    return OpportunityAggregator().aggregate([signal])[0]


class Provider:
    def get_product(self, asin):
        product=KeepaProduct(asin,5,"amazon.co.jp","fixture",5500,None,10000,4,NOW.isoformat())
        return KeepaResult(product,KeepaTokenMetadata(90,2,5,1000),False,None)


class ComparisonContractTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.db=Database(Path(self.temp.name)/"comparison.db"); self.db.migrate()
        self.settings=settings(self.db.path)

    def test_signal_metadata_propagates_to_opportunity_and_entry_snapshot(self):
        item=opportunity()
        self.assertEqual((item.source_type,item.source_id,item.strategy_version),
                         ("seller_monitor","SELLER00001","seller_monitor_v1"))
        snapshot=VirtualPurchaseService(self.settings).create(item,created_at=NOW.isoformat()).entry_snapshot
        self.assertEqual((snapshot.source_type,snapshot.source_id,snapshot.strategy_version),
                         ("seller_monitor","SELLER00001","seller_monitor_v1"))
        self.assertEqual((snapshot.evaluation_rule_version,snapshot.measurement_window_version,snapshot.fee_model_version),
                         ("vp_eval_v1","vp_window_v1","estimate_v1"))
        self.assertEqual(snapshot.comparison_contract.source_id,"SELLER00001")

    def test_unknown_source_is_not_inferred(self):
        snapshot=VirtualPurchaseService(self.settings).create(opportunity(sourced=False),created_at=NOW.isoformat()).entry_snapshot
        self.assertEqual((snapshot.source_type,snapshot.source_id,snapshot.strategy_version),
                         ("legacy","unknown","legacy"))

    def test_contract_is_immutable_on_virtual_purchase_update(self):
        purchase=VirtualPurchaseService(self.settings).create(opportunity(),created_at=NOW.isoformat())
        self.db.save_virtual_purchase(purchase)
        altered=replace(purchase,entry_snapshot=replace(purchase.entry_snapshot,strategy_version="changed_v2"))
        self.db.save_virtual_purchase(altered)
        loaded=self.db.load_virtual_purchases()[0]
        self.assertEqual(loaded.entry_snapshot.strategy_version,"seller_monitor_v1")

    def test_contract_columns_support_future_grouping(self):
        purchase=VirtualPurchaseService(self.settings).create(opportunity(),created_at=NOW.isoformat())
        self.db.save_virtual_purchase(purchase)
        with self.db.connect() as c:
            row=c.execute("""SELECT source_type,source_id,strategy_version,evaluation_rule_version,
                measurement_window_version,fee_model_version FROM virtual_purchases""").fetchone()
        self.assertEqual(tuple(row),("seller_monitor","SELLER00001","seller_monitor_v1","vp_eval_v1","vp_window_v1","estimate_v1"))

    def test_tracking_cost_records_only_measured_keepa_tokens(self):
        purchase=VirtualPurchaseService(self.settings).create(opportunity(),created_at=NOW.isoformat())
        self.db.save_virtual_purchase(purchase)
        service=VirtualPurchaseTrackingService(
            self.settings,self.db,Provider(),lambda db,now=None:{"status":"HEALTHY","tokens_left":100},
        )
        service.run(now=NOW)
        with self.db.connect() as c:
            row=c.execute("""SELECT keepa_tokens,api_calls,ai_calls,manual_review_count
                FROM virtual_purchase_tracking_costs""").fetchone()
        self.assertEqual(tuple(row),(2,None,None,None))


if __name__ == "__main__": unittest.main()
