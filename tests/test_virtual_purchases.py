from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.cli import main
from app.config import Settings
from app.opportunities.aggregator import OpportunityAggregator
from app.opportunities.models import Signal
from app.storage.db import Database
from app.virtual_purchases.models import FollowUpObservation, VirtualPurchaseStatus
from app.virtual_purchases.service import VirtualPurchaseService


BASE=datetime(2026,1,1,tzinfo=timezone.utc)


def settings(): return Settings(Path("unused.db"),500,0.15,85,"INFO")


def opportunity(asin="B0VPTEST01",observed_at=None,with_prices=True):
    observed_at=observed_at or BASE.isoformat()
    evidence={"sales_rank":12000,"new_offer_count":4,"amazon_owned":False,"median_price_30d":5500,"median_price_90d":5600}
    if with_prices: evidence.update({"purchase_price":3000,"expected_sale_price":5500,"profit_yen":1500,"roi":0.5})
    signal=Signal("amazon_arbitrage","amazon_arbitrage",asin,None,observed_at,80,True,"reason",evidence,confidence=85,quality="complete",product_name="fixture")
    return OpportunityAggregator().aggregate([signal])[0]


def at(day): return (BASE+timedelta(days=day)).isoformat()


class VirtualPurchaseTests(unittest.TestCase):
    def setUp(self): self.service=VirtualPurchaseService(settings())

    def create(self,**kwargs): return self.service.create(opportunity(**kwargs),created_at=BASE.isoformat())

    def observe(self,purchase,day,price,quality="complete"):
        return self.service.add_observation(purchase,FollowUpObservation(purchase.virtual_purchase_id,at(day),price,12000,4,False,quality))

    def test_opportunity_creates_virtual_purchase_and_snapshot(self):
        purchase=self.create()
        self.assertEqual((purchase.entry_snapshot.entry_price,purchase.entry_snapshot.expected_sale_price),(3000,5500))
        self.assertEqual(purchase.entry_snapshot.signal_types,("amazon_arbitrage",))

    def test_eligibility_requires_open_and_both_prices(self):
        self.assertTrue(self.service.eligibility(opportunity()).eligible)
        missing=self.service.eligibility(opportunity(with_prices=False))
        self.assertFalse(missing.eligible); self.assertIn("missing_purchase_price",missing.reasons)
        closed=replace(opportunity(),status="CLOSED")
        self.assertFalse(self.service.eligibility(closed).eligible)

    def test_ineligible_opportunity_is_rejected(self):
        with self.assertRaisesRegex(ValueError,"missing_purchase_price"):
            self.service.create(opportunity(with_prices=False),created_at=BASE.isoformat())

    def test_same_opportunity_observation_has_deterministic_id(self):
        self.assertEqual(self.create().virtual_purchase_id,self.create().virtual_purchase_id)

    def test_same_asin_different_opportunity_observation_can_create_new_purchase(self):
        first=self.create(observed_at=at(0)); second=self.create(observed_at=at(1))
        self.assertNotEqual(first.virtual_purchase_id,second.virtual_purchase_id)

    def test_entry_snapshot_does_not_follow_opportunity_updates(self):
        source=opportunity(); purchase=self.service.create(source,created_at=BASE.isoformat())
        changed=replace(source,summary=replace(source.summary,purchase_price=1000))
        self.assertEqual(purchase.entry_snapshot.entry_price,3000); self.assertEqual(changed.summary.purchase_price,1000)

    def test_observation_is_added_and_duplicate_time_is_ignored(self):
        purchase=self.create(); observation=FollowUpObservation(purchase.virtual_purchase_id,at(7),4000)
        purchase=self.service.add_observation(purchase,observation); duplicate=self.service.add_observation(purchase,observation)
        self.assertEqual(len(duplicate.observations),1)

    def test_seven_day_price_can_win(self):
        result=self.service.evaluate(self.observe(self.create(),7,5500),as_of=at(7))
        self.assertEqual(result.status,VirtualPurchaseStatus.WIN); self.assertEqual(result.outcome.days_to_first_win,7)

    def test_fourteen_day_first_win(self):
        purchase=self.observe(self.create(),7,3500); purchase=self.observe(purchase,14,5500)
        result=self.service.evaluate(purchase,as_of=at(14))
        self.assertEqual(result.outcome.days_to_first_win,14)

    def test_thirty_day_win_is_allowed(self):
        result=self.service.evaluate(self.observe(self.create(),30,5500),as_of=at(30))
        self.assertEqual(result.status,VirtualPurchaseStatus.WIN)

    def test_before_deadline_without_win_stays_open(self):
        result=self.service.evaluate(self.observe(self.create(),7,3500),as_of=at(7))
        self.assertEqual(result.status,VirtualPurchaseStatus.OPEN)

    def test_deadline_with_known_unprofitable_price_is_loss(self):
        result=self.service.evaluate(self.observe(self.create(),30,3500),as_of=at(30))
        self.assertEqual(result.status,VirtualPurchaseStatus.LOSS)

    def test_deadline_with_unknown_price_is_expired_not_loss(self):
        result=self.service.evaluate(self.observe(self.create(),30,None,"insufficient"),as_of=at(30))
        self.assertEqual(result.status,VirtualPurchaseStatus.EXPIRED)
        self.assertIsNone(result.outcome.best_observed_price)

    def test_best_worst_max_profit_and_roi(self):
        purchase=self.observe(self.create(),7,4000); purchase=self.observe(purchase,14,6000)
        result=self.service.evaluate(purchase,as_of=at(14))
        self.assertEqual((result.outcome.best_observed_price,result.outcome.worst_observed_price),(6000,4000))
        self.assertEqual(result.outcome.max_potential_profit_yen,1950); self.assertEqual(result.outcome.max_potential_roi,0.65)

    def test_frontend_summary_has_latest_virtual_values(self):
        result=self.service.evaluate(self.observe(self.create(),7,5500),as_of=at(7))
        self.assertEqual((result.summary.asin,result.summary.entry_price,result.summary.latest_price),("B0VPTEST01",3000,5500))
        self.assertEqual((result.summary.current_potential_profit_yen,result.summary.status),(1500,VirtualPurchaseStatus.WIN))

    def test_database_persists_snapshot_and_deduplicates_observation(self):
        with tempfile.TemporaryDirectory() as raw:
            db=Database(Path(raw)/"virtual.db"); db.migrate()
            purchase=self.observe(self.create(),7,5500); purchase=self.service.evaluate(purchase,as_of=at(7))
            db.save_virtual_purchase(purchase); db.save_virtual_purchase(purchase)
            with db.connect() as connection:
                row=connection.execute("SELECT entry_price,status,snapshot_json FROM virtual_purchases").fetchone()
                self.assertEqual((row[0],row[1]),(3000,"WIN")); self.assertEqual(json.loads(row[2])["entry_price"],3000)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM virtual_purchase_observations").fetchone()[0],1)

    def test_database_upsert_never_rewrites_entry_snapshot(self):
        with tempfile.TemporaryDirectory() as raw:
            db=Database(Path(raw)/"snapshot.db"); db.migrate(); purchase=self.create()
            db.save_virtual_purchase(purchase)
            altered=replace(purchase,entry_snapshot=replace(purchase.entry_snapshot,entry_price=999))
            db.save_virtual_purchase(altered)
            with db.connect() as connection:
                row=connection.execute("SELECT entry_price,snapshot_json FROM virtual_purchases").fetchone()
            self.assertEqual(row[0],3000); self.assertEqual(json.loads(row[1])["entry_price"],3000)

    def test_cli_mock_status_counts_and_zero_keepa_usage(self):
        with tempfile.TemporaryDirectory() as raw, patch("app.config._load_dotenv"), patch.dict(os.environ,{"APP_DB_PATH":str(Path(raw)/"cli.db")},clear=True), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["virtual-purchases","--mode","mock"]),0)
            report=json.loads(output.getvalue())
            self.assertEqual((report["total"],report["win"],report["loss"],report["open"],report["expired"]),(5,2,1,1,1))
            self.assertEqual(report["average_days_to_win"],10.5)
            with Database(Path(raw)/"cli.db").connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM keepa_usage").fetchone()[0],0)


if __name__ == "__main__": unittest.main()
