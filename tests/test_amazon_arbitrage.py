import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from app.cli import engine_registry, main
from app.config import FeatureFlags, Settings
from app.engines.amazon_arbitrage import AmazonArbitrageEngine, MockArbitrageSource
from app.engines.base import EngineContext, EngineStatus
from app.engines.registry import EngineRegistry
from app.services.amazon_arbitrage import ArbitrageInput, evaluate_arbitrage
from app.storage.db import Database


class AmazonArbitrageTests(unittest.TestCase):
    def settings(self,path,enabled=True):
        return Settings(path,500,.15,85,"INFO",FeatureFlags(amazon_arbitrage=enabled))

    def context(self,raw,enabled=True):
        path=Path(raw)/"test.db"
        return EngineContext(self.settings(path,enabled),Database(path),"mock")

    def assessments(self,raw):
        engine=AmazonArbitrageEngine(MockArbitrageSource())
        result=engine.run(self.context(raw))
        return result,{assessment.item.title:assessment for assessment in engine.last_assessments}

    def test_engine_is_registered_and_feature_flag_controls_run(self):
        self.assertIsInstance(engine_registry().get("amazon_arbitrage"),AmazonArbitrageEngine)
        with tempfile.TemporaryDirectory() as raw:
            events=[]
            class Source:
                def load(self): events.append("called"); return []
            registry=EngineRegistry(); registry.register(AmazonArbitrageEngine(Source()))
            off=registry.run_one("amazon_arbitrage",self.context(raw,False))
            on=registry.run_one("amazon_arbitrage",self.context(raw,True))
            self.assertEqual((off.status,on.status),(EngineStatus.SKIPPED,EngineStatus.SUCCESS))
            self.assertEqual(events,["called"])

    def test_fixture_detects_strong_candidate_and_separates_prices(self):
        with tempfile.TemporaryDirectory() as raw:
            result,rows=self.assessments(raw); strong=rows["strong"]
            self.assertEqual(result.processed_count,7)
            self.assertTrue(strong.is_candidate)
            self.assertEqual((strong.item.purchase_price,strong.expected_sale_price),(3000,5500))
            self.assertEqual((strong.absolute_drop_yen,strong.drop_rate),(2500,0.4545))
            self.assertGreaterEqual(strong.profit.profit_yen,1000)
            self.assertIn("price_drop=45.5%",strong.reason)

    def test_fixture_reject_reasons(self):
        with tempfile.TemporaryDirectory() as raw:
            _,rows=self.assessments(raw)
            self.assertIn("insufficient_profit",rows["low-profit"].reject_reasons)
            self.assertIn("insufficient_roi",rows["low-roi"].reject_reasons)
            self.assertIn("insufficient_price_drop",rows["small-drop"].reject_reasons)
            self.assertIn("missing_expected_sale_price",rows["missing-history"].reject_reasons)

    def test_missing_rank_is_not_zero_and_lowers_confidence(self):
        with tempfile.TemporaryDirectory() as raw:
            _,rows=self.assessments(raw)
            missing=rows["missing-rank"]; strong=rows["strong"]
            self.assertTrue(missing.is_candidate)
            self.assertIsNone(missing.evidence["sales_rank"])
            self.assertIn("sales_rank",missing.evidence["missing"])
            self.assertLess(missing.confidence,strong.confidence)

    def test_amazon_owned_is_risk_not_automatic_reject(self):
        with tempfile.TemporaryDirectory() as raw:
            _,rows=self.assessments(raw)
            owned=rows["amazon-owned"]; strong=rows["strong"]
            self.assertTrue(owned.is_candidate)
            self.assertTrue(owned.evidence["amazon_owned_return_risk"])
            self.assertLess(owned.arbitrage_score,strong.arbitrage_score)

    def test_score_is_bounded_and_demand_competition_reject(self):
        with tempfile.TemporaryDirectory() as raw:
            settings=self.settings(Path(raw)/"db")
            weak=evaluate_arbitrage(ArbitrageInput("B0ARB00008","weak",3000,5500,sales_rank=300000,new_offer_count=20),settings)
            self.assertIn("weak_demand",weak.reject_reasons)
            self.assertIn("excessive_competition",weak.reject_reasons)
            for item in MockArbitrageSource().load():
                self.assertGreaterEqual(evaluate_arbitrage(item,settings).arbitrage_score,0)
                self.assertLessEqual(evaluate_arbitrage(item,settings).arbitrage_score,100)

    def test_cli_mock_uses_no_keepa_api(self):
        with tempfile.TemporaryDirectory() as raw, patch.dict(os.environ,{"APP_DB_PATH":str(Path(raw)/"cli.db"),"ENGINE_AMAZON_ARBITRAGE_ENABLED":"true"},clear=False), patch("app.providers.keepa.KeepaHttpClient.get_product",side_effect=AssertionError("Keepa API called")), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["run","amazon-arbitrage","--mode","mock"]),0)
            self.assertIn("engine=amazon_arbitrage status=success",output.getvalue())


if __name__ == "__main__":
    unittest.main()
