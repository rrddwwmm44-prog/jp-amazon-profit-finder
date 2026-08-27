from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from app.cli import main
from app.config import Settings
from app.opportunities.aggregator import OpportunityAggregator
from app.opportunities.models import Signal
from app.storage.db import Database
from app.strategy_performance.fixtures import mock_performance_samples
from app.strategy_performance.models import PerformanceSample, SampleQuality
from app.strategy_performance.service import StrategyPerformanceService
from app.virtual_purchases.models import FollowUpObservation, VirtualPurchaseStatus
from app.virtual_purchases.service import VirtualPurchaseService


def sample(identifier="vp",signals=("amazon_arbitrage",),status="WIN",source="DEFAULT_ESTIMATE",version="estimate_v1",score=80,profit=1000,roi=.3,days=7,owned=False,rank=12000,offers=4,confidence=80,quality="complete"):
    return PerformanceSample(identifier,signals,source,version,VirtualPurchaseStatus(status),score,confidence,quality,owned,rank,offers,profit,roi,days)


def bucket_map(items): return {item.bucket_key:item.metrics for item in items}


class StrategyPerformanceTests(unittest.TestCase):
    def setUp(self): self.service=StrategyPerformanceService()

    def report(self,samples): return self.service.analyze(samples)[0]

    def test_strategy_identity_and_signal_order_are_normalized(self):
        report=self.report([sample("a",("seller_decline","amazon_arbitrage")),sample("b",("amazon_arbitrage","seller_decline"),status="LOSS")])
        self.assertEqual(len(report.strategies),1)
        self.assertEqual(report.strategies[0].strategy_key,"amazon_arbitrage+seller_decline")

    def test_fee_models_are_never_mixed(self):
        reports=self.service.analyze([sample("a"),sample("b",source="MANUAL",version="manual_v1")])
        self.assertEqual(len(reports),2)
        self.assertEqual({(item.fee_source,item.fee_model_version) for item in reports},{("DEFAULT_ESTIMATE","estimate_v1"),("MANUAL","manual_v1")})

    def test_fee_model_filter(self):
        reports=self.service.analyze([sample("a"),sample("b",source="MANUAL",version="manual_v1")],fee_source="MANUAL",fee_model_version="manual_v1")
        self.assertEqual((len(reports),reports[0].overall.total_count),(1,1))

    def test_win_loss_are_the_only_closed_denominator(self):
        metrics=self.report([sample("w"),sample("l",status="LOSS"),sample("o",status="OPEN"),sample("e",status="EXPIRED")]).overall
        self.assertEqual((metrics.total_count,metrics.closed_count,metrics.win_count,metrics.loss_count,metrics.open_count,metrics.expired_count),(4,2,1,1,1,1))
        self.assertEqual(metrics.win_rate,.5)

    def test_open_is_counted_but_excluded_from_win_rate(self):
        metrics=self.report([sample("w"),sample("o",status="OPEN")]).overall
        self.assertEqual((metrics.closed_count,metrics.open_count,metrics.win_rate),(1,1,1.0))

    def test_expired_is_counted_but_excluded_from_win_rate(self):
        metrics=self.report([sample("l",status="LOSS"),sample("e",status="EXPIRED")]).overall
        self.assertEqual((metrics.closed_count,metrics.expired_count,metrics.win_rate),(1,1,0.0))

    def test_closed_zero_has_unknown_win_rate(self):
        metrics=self.report([sample("o",status="OPEN"),sample("e",status="EXPIRED")]).overall
        self.assertIsNone(metrics.win_rate)

    def test_profit_average_and_median_ignore_missing(self):
        metrics=self.report([sample("a",profit=1000),sample("b",profit=3000),sample("c",profit=None)]).overall
        self.assertEqual((metrics.average_max_potential_profit_yen,metrics.median_max_potential_profit_yen),(2000,2000))

    def test_roi_average_and_median_ignore_missing(self):
        metrics=self.report([sample("a",roi=.2),sample("b",roi=.6),sample("c",roi=None)]).overall
        self.assertEqual((metrics.average_max_potential_roi,metrics.median_max_potential_roi),(.4,.4))

    def test_days_to_win_only_uses_wins(self):
        metrics=self.report([sample("a",days=7),sample("b",days=21),sample("c",status="LOSS",days=1)]).overall
        self.assertEqual((metrics.average_days_to_win,metrics.median_days_to_win),(14,14))

    def test_score_buckets(self):
        buckets=bucket_map(self.report([sample("a",score=55),sample("b",score=65),sample("c",score=75),sample("d",score=85),sample("e",score=95)]).score_buckets)
        self.assertEqual(set(buckets),{"0-59","60-69","70-79","80-89","90-100"})

    def test_signal_count_buckets(self):
        buckets=bucket_map(self.report([sample("a"),sample("b",("a","b")),sample("c",("a","b","c"))]).signal_count_buckets)
        self.assertEqual(set(buckets),{"1","2","3+"})

    def test_amazon_owned_keeps_true_false_unknown_separate(self):
        buckets=bucket_map(self.report([sample("a",owned=True),sample("b",owned=False),sample("c",owned=None)]).amazon_owned_buckets)
        self.assertEqual(set(buckets),{"true","false","unknown"})

    def test_sales_rank_buckets_and_unknown(self):
        buckets=bucket_map(self.report([sample("a",rank=5000),sample("b",rank=30000),sample("c",rank=100000),sample("d",rank=200000),sample("e",rank=None)]).sales_rank_buckets)
        self.assertEqual(set(buckets),{"1-10000","10001-50000","50001-150000","150001+","unknown"})

    def test_offer_count_buckets_and_unknown(self):
        buckets=bucket_map(self.report([sample("a",offers=0),sample("b",offers=5),sample("c",offers=10),sample("d",offers=20),sample("e",offers=None)]).new_offer_count_buckets)
        self.assertEqual(set(buckets),{"0-3","4-7","8-15","16+","unknown"})

    def test_confidence_and_history_quality_buckets(self):
        report=self.report([sample("a",confidence=55,quality="complete"),sample("b",confidence=None,quality=None)])
        self.assertEqual(set(bucket_map(report.confidence_buckets)),{"0-59","unknown"})
        self.assertEqual(set(bucket_map(report.history_quality_buckets)),{"complete","unknown"})

    def test_all_missing_metrics_stay_unknown_not_zero(self):
        metrics=self.report([sample("a",status="EXPIRED",profit=None,roi=None,days=None,rank=None,offers=None,owned=None)]).overall
        self.assertIsNone(metrics.win_rate); self.assertIsNone(metrics.average_max_potential_profit_yen)
        self.assertIsNone(metrics.average_max_potential_roi); self.assertIsNone(metrics.average_days_to_win)

    def test_sample_quality_thresholds(self):
        insufficient=self.report([sample(str(i)) for i in range(9)]).overall
        early=self.report([sample(str(i)) for i in range(10)]).overall
        usable=self.report([sample(str(i)) for i in range(30)]).overall
        self.assertEqual((insufficient.sample_quality,early.sample_quality,usable.sample_quality),(SampleQuality.INSUFFICIENT,SampleQuality.EARLY,SampleQuality.USABLE))

    def test_drilldown_ids_are_retained(self):
        metrics=self.report([sample("vp-a"),sample("vp-b",status="LOSS")]).overall
        self.assertEqual(metrics.virtual_purchase_ids,("vp-a","vp-b"))

    def test_mock_fixture_has_required_cases_and_three_strategies(self):
        samples=mock_performance_samples(); report=self.report(samples)
        self.assertGreaterEqual(len(samples),10); self.assertEqual(len(report.strategies),3)
        self.assertEqual((report.overall.win_count,report.overall.loss_count,report.overall.open_count,report.overall.expired_count),(5,5,1,1))

    def test_report_contains_all_frontend_analysis_sections(self):
        report=self.report(mock_performance_samples())
        self.assertTrue(report.strategies); self.assertTrue(report.score_buckets)
        self.assertTrue(report.signal_count_buckets); self.assertTrue(report.amazon_owned_buckets)
        self.assertTrue(report.sales_rank_buckets); self.assertTrue(report.new_offer_count_buckets)

    def test_strategy_metrics_retain_fee_identity(self):
        strategy=self.report([sample("a")]).strategies[0]
        self.assertEqual((strategy.fee_source,strategy.fee_model_version),("DEFAULT_ESTIMATE","estimate_v1"))

    def test_virtual_purchase_adapter_and_database_read(self):
        settings=Settings(Path("unused.db"),500,.15,85,"INFO")
        signal=Signal("amazon_arbitrage","amazon_arbitrage","B0PERF0001",None,"2026-01-01T00:00:00+00:00",80,True,"reason",{"purchase_price":3000,"expected_sale_price":5500,"profit_yen":1500,"roi":.5},product_name="fixture")
        opportunity=OpportunityAggregator().aggregate([signal])[0]
        service=VirtualPurchaseService(settings); purchase=service.create(opportunity,created_at="2026-01-01T00:00:00+00:00")
        purchase=service.add_observation(purchase,FollowUpObservation(purchase.virtual_purchase_id,"2026-01-08T00:00:00+00:00",5500)); purchase=service.evaluate(purchase,as_of="2026-01-08T00:00:00+00:00")
        self.assertEqual(self.service.sample_from_purchase(purchase).strategy_key,"amazon_arbitrage")
        with tempfile.TemporaryDirectory() as raw:
            db=Database(Path(raw)/"performance.db"); db.migrate(); db.save_virtual_purchase(purchase)
            reports=self.service.analyze_database(db)
            self.assertEqual((reports[0].overall.total_count,reports[0].overall.win_count),(1,1))

    def test_cli_mock_frontend_summary_and_zero_keepa_usage(self):
        with tempfile.TemporaryDirectory() as raw, patch("app.config._load_dotenv"), patch.dict(os.environ,{"APP_DB_PATH":str(Path(raw)/"cli.db")},clear=True), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["strategy-performance","--mode","mock"]),0)
            report=json.loads(output.getvalue())
            self.assertEqual((report["overall"]["total_count"],len(report["strategies"])),(12,3))
            self.assertIn("score_buckets",report); self.assertIn("signal_count",report)
            with Database(Path(raw)/"cli.db").connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM keepa_usage").fetchone()[0],0)


if __name__ == "__main__": unittest.main()
