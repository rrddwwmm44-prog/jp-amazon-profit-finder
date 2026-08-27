from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.opportunities.aggregator import OpportunityAggregator
from app.opportunities.models import Signal
from app.services.amazon_arbitrage import ArbitrageInput, evaluate_arbitrage
from app.services.profit_calculator import (
    CalculationContext, FeeModel, FeeSource, FulfillmentMethod, calculate,
)
from app.storage.db import Database
from app.virtual_purchases.models import FollowUpObservation, VirtualPurchaseStatus
from app.virtual_purchases.service import VirtualPurchaseService


NOW="2026-01-01T00:00:00+00:00"


def settings(): return Settings(Path("unused.db"),500,0.15,85,"INFO")


def opportunity():
    signal=Signal("amazon_arbitrage","amazon_arbitrage","B0FEE00001",None,NOW,80,True,"reason",{
        "purchase_price":3000,"expected_sale_price":5500,"profit_yen":1500,"roi":0.5,
        "sales_rank":12000,"new_offer_count":4,"amazon_owned":False,
    },confidence=80,quality="complete",product_name="fee fixture")
    return OpportunityAggregator().aggregate([signal])[0]


class ProfitFeeModelTests(unittest.TestCase):
    def test_legacy_calculate_result_is_compatible(self):
        result=calculate(5500,3000)
        self.assertEqual((result.profit_yen,result.margin,result.roi),(1500,0.2727,0.5))

    def test_referral_and_fulfillment_fees(self):
        result=calculate(5500,3000)
        self.assertEqual((result.referral_fee,result.fulfillment_fee),(550,450))

    def test_total_fees_and_total_cost(self):
        result=calculate(5500,3000)
        self.assertEqual((result.total_fees,result.total_cost),(1000,4000))

    def test_shipping_and_other_cost_remain_legacy_compatible(self):
        result=calculate(5500,3000,100,.10,450,50)
        self.assertEqual((result.profit_yen,result.total_cost,result.roi),(1350,4150,0.4355))

    def test_default_fee_metadata(self):
        result=calculate(5500,3000)
        self.assertEqual(result.fee_source,FeeSource.DEFAULT_ESTIMATE)
        self.assertEqual(result.fee_model_version,"estimate_v1"); self.assertTrue(result.calculated_at)

    def test_manual_fee_model_is_applied(self):
        model=FeeModel(.15,600,100,50,FeeSource.MANUAL,"manual_v1",NOW)
        result=calculate(5500,3000,fee_model=model)
        self.assertEqual((result.referral_fee,result.total_fees,result.total_cost,result.profit_yen),(825,1425,4575,925))
        self.assertEqual((result.fee_source,result.fee_model_version),(FeeSource.MANUAL,"manual_v1"))

    def test_unknown_category_and_fulfillment_are_safe(self):
        context=CalculationContext("B0FEE00001",None,FulfillmentMethod.UNKNOWN,5500,3000)
        self.assertEqual(calculate(5500,3000,context=context).profit_yen,1500)

    def test_arbitrage_keeps_existing_candidate_and_exposes_fee_version(self):
        assessment=evaluate_arbitrage(ArbitrageInput("B0FEE00001","fixture",3000,5500,5600,sales_rank=12000,new_offer_count=4,amazon_owned=False,demand_quality="good"),settings())
        self.assertTrue(assessment.is_candidate); self.assertEqual(assessment.profit.profit_yen,1500)
        self.assertEqual(assessment.evidence["fee_model_version"],"estimate_v1")

    def test_arbitrage_accepts_manual_fee_model_without_threshold_changes(self):
        model=FeeModel(.10,450,fee_source=FeeSource.MANUAL,fee_model_version="manual_v1",calculated_at=NOW)
        assessment=evaluate_arbitrage(ArbitrageInput("B0FEE00001","fixture",3000,5500,5600,sales_rank=12000,new_offer_count=4,amazon_owned=False,demand_quality="good"),settings(),model)
        self.assertTrue(assessment.is_candidate)
        self.assertEqual((assessment.profit.fee_source,assessment.evidence["fee_model_version"]),(FeeSource.MANUAL,"manual_v1"))

    def test_virtual_purchase_snapshots_fee_model(self):
        model=FeeModel(.15,600,100,50,FeeSource.MANUAL,"manual_v1",NOW)
        purchase=VirtualPurchaseService(settings(),fee_model=model).create(opportunity(),created_at=NOW)
        snapshot=purchase.entry_snapshot
        self.assertEqual((snapshot.expected_profit_yen,snapshot.fee_source,snapshot.fee_model_version),(925,"MANUAL","manual_v1"))
        self.assertEqual((snapshot.referral_fee,snapshot.fulfillment_fee,snapshot.total_fees),(825,600,1425))

    def test_later_service_fee_change_does_not_change_past_outcome(self):
        original=VirtualPurchaseService(settings(),fee_model=FeeModel(.15,600,100,50,FeeSource.MANUAL,"manual_v1",NOW))
        purchase=original.create(opportunity(),created_at=NOW)
        purchase=original.add_observation(purchase,FollowUpObservation(purchase.virtual_purchase_id,"2026-01-08T00:00:00+00:00",6000))
        changed=VirtualPurchaseService(settings(),fee_model=FeeModel(.50,2000,fee_source=FeeSource.MANUAL,fee_model_version="manual_v2"))
        result=changed.evaluate(purchase,as_of="2026-01-08T00:00:00+00:00")
        self.assertEqual(result.status,VirtualPurchaseStatus.WIN)
        self.assertEqual((result.outcome.max_potential_profit_yen,result.entry_snapshot.fee_model_version),(1350,"manual_v1"))

    def test_database_persists_fee_model_columns_and_snapshot(self):
        with tempfile.TemporaryDirectory() as raw:
            db=Database(Path(raw)/"fees.db"); db.migrate()
            purchase=VirtualPurchaseService(settings()).create(opportunity(),created_at=NOW); db.save_virtual_purchase(purchase)
            with db.connect() as connection:
                row=connection.execute("SELECT fee_source,fee_model_version,referral_fee,fulfillment_fee,total_fees,snapshot_json FROM virtual_purchases").fetchone()
            self.assertEqual(tuple(row[:5]),("DEFAULT_ESTIMATE","estimate_v1",550,450,1000))
            self.assertEqual(json.loads(row[5])["fee_model_version"],"estimate_v1")


if __name__ == "__main__": unittest.main()
