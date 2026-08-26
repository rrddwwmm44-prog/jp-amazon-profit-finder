from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app.cli import main
from app.config import Settings
from app.domain import MissingState
from app.opportunities.adapters import arbitrage_to_signal, seller_decline_to_signal
from app.opportunities.aggregator import OpportunityAggregator
from app.opportunities.models import Signal
from app.services.amazon_arbitrage import ArbitrageInput, evaluate_arbitrage
from app.services.seller_decline import SellerDeclineInput, SellerObservation, evaluate_seller_decline
from app.storage.db import Database


NOW="2026-08-27T00:00:00+00:00"


def settings(): return Settings(Path("unused.db"),500,0.15,85,"INFO")


def signal(kind="amazon_arbitrage",asin="B0OPP00001",jan=None,score=78,candidate=True,**kwargs):
    evidence={"purchase_price":3000,"expected_sale_price":5500,"profit_yen":1500,"roi":0.5,
              "sales_rank":12000,"new_offer_count":4,"amazon_owned":False,
              "median_price_30d":5500,"median_price_90d":5600}
    evidence.update(kwargs.pop("evidence",{}))
    return Signal(kind,kind,asin,jan,NOW,score,candidate,kwargs.pop("reason","reason"),evidence,
                  confidence=kwargs.pop("confidence",80),quality=kwargs.pop("quality","complete"),
                  product_name=kwargs.pop("product_name","fixture"),**kwargs)


def seller_assessment():
    def seen(value): return SellerObservation(value,MissingState.VERIFIED_ZERO if value==0 else None)
    item=SellerDeclineInput("B0OPP00001","fixture",seen(4),seen(8),seen(13),seen(18),5480,4800,4200,3980,26000,25000,False,NOW)
    return evaluate_seller_decline(item,settings())


class OpportunityTests(unittest.TestCase):
    def setUp(self): self.aggregator=OpportunityAggregator()

    def test_arbitrage_assessment_converts_to_signal(self):
        assessment=evaluate_arbitrage(ArbitrageInput("B0OPP00001","fixture",3000,5500,5600,sales_rank=12000,new_offer_count=4,amazon_owned=False,demand_quality="good",observed_at=NOW),settings())
        result=arbitrage_to_signal(assessment,NOW)
        self.assertTrue(result.candidate); self.assertEqual(result.signal_type,"amazon_arbitrage")

    def test_seller_decline_assessment_converts_to_signal(self):
        result=seller_decline_to_signal(seller_assessment(),NOW)
        self.assertTrue(result.candidate); self.assertEqual(result.signal_type,"seller_decline")

    def test_same_asin_combines_and_keeps_both_signals(self):
        items=self.aggregator.aggregate([signal(),signal("seller_decline",score=82)])
        self.assertEqual((len(items),items[0].signal_count),(1,2))

    def test_same_jan_combines_without_asin(self):
        items=self.aggregator.aggregate([signal(asin=None,jan="4901234567894"),signal("seller_decline",asin=None,jan="4901234567894")])
        self.assertEqual(len(items),1); self.assertEqual(items[0].identity_type,"jan")

    def test_asin_takes_priority_over_jan(self):
        item=self.aggregator.aggregate([signal(jan="4901234567894")])[0]
        self.assertEqual((item.identity_type,item.identity_value),("asin","B0OPP00001"))

    def test_different_asins_do_not_combine(self):
        self.assertEqual(len(self.aggregator.aggregate([signal(),signal(asin="B0OPP00002")])),2)

    def test_missing_identity_never_combines_by_name(self):
        items=self.aggregator.aggregate([signal(asin=None),signal("seller_decline",asin=None)])
        self.assertEqual(len(items),2)

    def test_rejected_signal_is_not_promoted(self):
        items=self.aggregator.aggregate([signal(),signal("seller_decline",candidate=False)])
        self.assertEqual(items[0].signal_count,1)

    def test_combined_score_and_synergy(self):
        item=self.aggregator.aggregate([signal(score=78),signal("seller_decline",score=82)])[0]
        self.assertEqual(item.opportunity_score,97)

    def test_score_is_capped_at_100(self):
        item=self.aggregator.aggregate([signal(score=98),signal("seller_decline",score=99)])[0]
        self.assertEqual(item.opportunity_score,100)

    def test_commerce_and_keepa_summary_are_frontend_ready(self):
        summary=self.aggregator.aggregate([signal()])[0].summary
        self.assertEqual((summary.purchase_price,summary.expected_profit_yen,summary.roi),(3000,1500,0.5))
        self.assertEqual((summary.sales_rank,summary.new_offer_count,summary.amazon_owned),(12000,4,False))
        self.assertEqual(summary.signal_types,("amazon_arbitrage",))

    def test_seller_signal_supplies_marketplace_summary(self):
        seller=signal("seller_decline",evidence={"marketplace_new_price":5480})
        summary=self.aggregator.aggregate([signal(),seller])[0].summary
        self.assertEqual(summary.marketplace_new_price,5480)

    def test_reasons_are_deduplicated(self):
        item=self.aggregator.aggregate([signal(),signal("seller_decline")])[0]
        self.assertEqual(item.reasons,("reason",))

    def test_explicit_risks_and_partial_quality_are_preserved(self):
        item=self.aggregator.aggregate([signal(evidence={"amazon_owned_return_risk":True},quality="partial")])[0]
        self.assertEqual(item.risks,("amazon_owned_return_risk","partial_history"))

    def test_confidence_uses_highest_observed_signal_value(self):
        item=self.aggregator.aggregate([signal(confidence=70),signal("seller_decline",confidence=85)])[0]
        self.assertEqual(item.confidence,85)

    def test_database_save_and_signal_deduplication(self):
        with tempfile.TemporaryDirectory() as raw:
            db=Database(Path(raw)/"opportunities.db"); db.migrate()
            item=self.aggregator.aggregate([signal(),signal("seller_decline")])[0]
            db.save_opportunity(item); db.save_opportunity(item)
            with db.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0],1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM opportunity_signals").fetchone()[0],2)

    def test_cli_mock_outputs_summary_and_consumes_no_keepa_tokens(self):
        with tempfile.TemporaryDirectory() as raw, patch("app.config._load_dotenv"), patch.dict(os.environ,{"APP_DB_PATH":str(Path(raw)/"cli.db")},clear=True), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["opportunities","--mode","mock"]),0)
            report=json.loads(output.getvalue())
            self.assertEqual((report["opportunity_count"],report["signal_count"],report["multi_signal_count"]),(1,2,1))
            db=Database(Path(raw)/"cli.db")
            with db.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM keepa_usage").fetchone()[0],0)


if __name__ == "__main__": unittest.main()
