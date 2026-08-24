import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from app.cli import engine_registry, main
from app.config import FeatureFlags, Settings
from app.domain import MissingState
from app.engines.base import EngineContext, EngineStatus
from app.engines.registry import EngineRegistry
from app.engines.seller_decline import MockSellerDeclineSource, SellerDeclineEngine
from app.services.seller_decline import DemandTrend, SellerObservation
from app.storage.db import Database


class SellerDeclineTests(unittest.TestCase):
    def settings(self,path,enabled=True):
        return Settings(path,500,.15,85,"INFO",FeatureFlags(seller_decline=enabled))

    def context(self,raw,enabled=True):
        path=Path(raw)/"test.db"
        return EngineContext(self.settings(path,enabled),Database(path),"mock")

    def assessments(self,raw):
        engine=SellerDeclineEngine(MockSellerDeclineSource())
        result=engine.run(self.context(raw))
        return result,{assessment.item.title:assessment for assessment in engine.last_assessments}

    def test_registry_and_feature_flag(self):
        self.assertIsInstance(engine_registry().get("seller_decline"),SellerDeclineEngine)
        with tempfile.TemporaryDirectory() as raw:
            events=[]
            class Source:
                def load(self): events.append("called"); return []
            registry=EngineRegistry(); registry.register(SellerDeclineEngine(Source()))
            off=registry.run_one("seller_decline",self.context(raw,False))
            on=registry.run_one("seller_decline",self.context(raw,True))
            self.assertEqual((off.status,on.status),(EngineStatus.SKIPPED,EngineStatus.SUCCESS))
            self.assertEqual(events,["called"])

    def test_decline_rates_acceleration_and_price_trends(self):
        with tempfile.TemporaryDirectory() as raw:
            _,rows=self.assessments(raw); ideal=rows["ideal"]
            self.assertEqual(ideal.decline_rates,{"7d":0.5,"30d":0.6923,"90d":0.7778})
            self.assertGreater(ideal.decline_acceleration,0)
            self.assertEqual(ideal.price_trends,{"7d":0.1417,"30d":0.3048,"90d":0.3769})

    def test_ideal_supply_contraction_is_candidate(self):
        with tempfile.TemporaryDirectory() as raw:
            result,rows=self.assessments(raw); ideal=rows["ideal"]
            self.assertEqual(result.processed_count,9)
            self.assertTrue(ideal.is_candidate)
            self.assertTrue(ideal.supply_contraction_likely)
            self.assertEqual(ideal.demand_trend,DemandTrend.MAINTAINED)
            self.assertIn("seller_30d_decline=69.2%",ideal.reason)

    def test_demand_collapse_and_non_continuous_decline_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            _,rows=self.assessments(raw)
            collapse=rows["demand-collapse"]
            self.assertEqual(collapse.demand_trend,DemandTrend.WORSENING)
            self.assertTrue(collapse.demand_decline_risk)
            self.assertIn("demand_decline_risk",collapse.reject_reasons)
            self.assertIn("insufficient_seller_decline",rows["flat"].reject_reasons)
            self.assertIn("insufficient_seller_decline",rows["temporary"].reject_reasons)

    def test_missing_observation_is_not_zero_and_is_provisional(self):
        with tempfile.TemporaryDirectory() as raw:
            _,rows=self.assessments(raw)
            missing=rows["missing-current"]
            self.assertIsNone(missing.evidence["new_offer_count_current"])
            self.assertEqual(missing.evidence["new_offer_count_current_state"],"not_observed")
            self.assertTrue(missing.is_provisional)
            self.assertIn("insufficient_history",missing.reject_reasons)
            self.assertIsNone(missing.decline_rates["30d"])

    def test_zero_requires_verified_zero_and_is_processed(self):
        with self.assertRaises(ValueError): SellerObservation(0)
        verified=SellerObservation(0,MissingState.VERIFIED_ZERO)
        self.assertEqual(verified.value,0)
        with tempfile.TemporaryDirectory() as raw:
            _,rows=self.assessments(raw); zero=rows["verified-zero"]
            self.assertEqual(zero.evidence["new_offer_count_current_state"],"verified_zero")
            self.assertEqual(zero.decline_rates["30d"],1.0)
            self.assertTrue(zero.is_candidate)

    def test_amazon_owned_is_not_rejected_and_scores_are_bounded(self):
        with tempfile.TemporaryDirectory() as raw:
            _,rows=self.assessments(raw)
            self.assertTrue(rows["amazon-owned"].is_candidate)
            self.assertLess(rows["amazon-owned"].seller_decline_score,rows["ideal"].seller_decline_score)
            for assessment in rows.values():
                self.assertGreaterEqual(assessment.seller_decline_score,0)
                self.assertLessEqual(assessment.seller_decline_score,100)

    def test_cli_mock_uses_no_keepa_api(self):
        with tempfile.TemporaryDirectory() as raw, patch.dict(os.environ,{"APP_DB_PATH":str(Path(raw)/"cli.db"),"ENGINE_SELLER_DECLINE_ENABLED":"true"},clear=False), patch("app.providers.keepa.KeepaHttpClient.get_product",side_effect=AssertionError("Keepa API called")), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["run","seller-decline","--mode","mock"]),0)
            self.assertIn("engine=seller_decline status=success",output.getvalue())


if __name__ == "__main__":
    unittest.main()
