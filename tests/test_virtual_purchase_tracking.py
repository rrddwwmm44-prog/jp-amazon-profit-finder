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
from app.providers.keepa import KeepaError, KeepaProduct, KeepaResult, KeepaTokenMetadata, KeepaTokensExhausted
from app.services.keepa_history import HistoryQuality, NormalizedKeepaHistory
from app.storage.db import Database
from app.virtual_purchases.models import FollowUpObservation, VirtualPurchaseStatus
from app.virtual_purchases.service import VirtualPurchaseService
from app.virtual_purchases.tracking import VirtualPurchaseTrackingService, to_observation


NOW=datetime(2026,4,1,tzinfo=timezone.utc)


def make_settings(path: Path, **changes) -> Settings:
    return replace(Settings(path,500,0.15,85,"INFO"),**changes)


def make_purchase(settings: Settings, asin="B0TRACK001", *, age_days=10, score=80, urgency=50, observed_hours=None):
    created=NOW-timedelta(days=age_days)
    evidence={"purchase_price":3000,"expected_sale_price":5500,"profit_yen":1500,"roi":0.5}
    signal=Signal("amazon_arbitrage","amazon_arbitrage",asin,None,created.isoformat(),score,True,"tracking",evidence,confidence=80,urgency_hint=urgency,product_name=asin)
    opportunity=OpportunityAggregator().aggregate([signal])[0]
    purchase=VirtualPurchaseService(settings).create(opportunity,created_at=created.isoformat())
    if observed_hours is not None:
        purchase=VirtualPurchaseService(settings).add_observation(purchase,FollowUpObservation(purchase.virtual_purchase_id,(NOW-timedelta(hours=observed_hours)).isoformat(),3500))
    return purchase


def result(asin: str, price=3500, *, observed_at=None, cache=False, left=100, quality=HistoryQuality.COMPLETE):
    at=observed_at or NOW.isoformat()
    history=NormalizedKeepaHistory(asin,asin,at,price,price,price,price,float(price) if price else None,float(price) if price else None,12000,12000,12000,12000,4,5,6,7,None,None,False,quality)
    product=KeepaProduct(asin,5,"amazon.co.jp",asin,None,None,12000,4,at)
    tokens=None if cache else KeepaTokenMetadata(left,1,5,1000,0.0,10)
    return KeepaResult(product,tokens,cache,history)


class FakeProvider:
    def __init__(self, mapping): self.mapping=mapping; self.calls=[]
    def get_product(self,asin):
        self.calls.append(asin)
        value=self.mapping[asin]
        if isinstance(value,Exception): raise value
        return value


def budget(status="HEALTHY",left=100):
    return lambda db,now=None:{"status":status,"tokens_left":left}


class VirtualPurchaseTrackingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.db=Database(Path(self.temp.name)/"tracking.db"); self.db.migrate()
        self.settings=make_settings(self.db.path)

    def save(self,*items):
        for item in items: self.db.save_virtual_purchase(item)

    def service(self,provider=None,status="HEALTHY",left=100,settings=None):
        return VirtualPurchaseTrackingService(settings or self.settings,self.db,provider,budget(status,left))

    def test_open_due_is_selected(self):
        purchase=make_purchase(self.settings); due,eligible,skipped=self.service().select_due([purchase],NOW)
        self.assertEqual((len(due),eligible,skipped),(1,1,0))

    def test_interval_not_due_is_skipped(self):
        purchase=make_purchase(self.settings,observed_hours=1); due,eligible,skipped=self.service().select_due([purchase],NOW)
        self.assertEqual((len(due),eligible,skipped),(0,1,1))

    def test_closed_and_asinless_are_skipped(self):
        closed=replace(make_purchase(self.settings),status=VirtualPurchaseStatus.WIN)
        missing=replace(make_purchase(self.settings,"B0TRACK002"),asin=None)
        due,eligible,skipped=self.service().select_due([closed,missing],NOW)
        self.assertEqual((len(due),eligible,skipped),(0,1,1))

    def test_priority_is_score_then_urgency_then_age(self):
        a=make_purchase(self.settings,"B0TRACK001",score=80,urgency=90)
        b=make_purchase(self.settings,"B0TRACK002",score=90,urgency=1)
        c=make_purchase(self.settings,"B0TRACK003",score=80,urgency=80,age_days=20)
        due,_,_=self.service().select_due([a,c,b],NOW)
        self.assertEqual([x.purchase.asin for x in due],["B0TRACK002","B0TRACK001","B0TRACK003"])

    def test_budget_limits(self):
        service=self.service()
        self.assertEqual(service.plan_limit({"status":"HEALTHY","tokens_left":100}),20)
        self.assertEqual(service.plan_limit({"status":"LIMITED","tokens_left":100}),5)
        self.assertEqual(service.plan_limit({"status":"CRITICAL","tokens_left":100}),1)
        self.assertEqual(service.plan_limit({"status":"EXHAUSTED","tokens_left":100}),0)
        self.assertEqual(service.plan_limit({"status":"UNKNOWN","tokens_left":None}),1)

    def test_token_count_and_config_batch_limit(self):
        configured=make_settings(self.db.path,virtual_track_max_asins_per_run=2)
        self.assertEqual(self.service(settings=configured).plan_limit({"status":"HEALTHY","tokens_left":1}),1)

    def test_exhausted_and_unknown_bootstrap(self):
        items=[make_purchase(self.settings,f"B0TRACK00{i}") for i in range(1,4)]; self.save(*items)
        provider=FakeProvider({item.asin:result(item.asin) for item in items})
        exhausted=self.service(provider,"EXHAUSTED",0).run(now=NOW)
        self.assertEqual((exhausted.planned_count,len(provider.calls)),(0,0))
        unknown=self.service(provider,"UNKNOWN",None).run(now=NOW)
        self.assertEqual((unknown.planned_count,len(provider.calls)),(1,1))

    def test_cache_hit_consumes_no_live_token(self):
        purchase=make_purchase(self.settings); self.save(purchase)
        report=self.service(FakeProvider({purchase.asin:result(purchase.asin,cache=True)})).run(now=NOW)
        self.assertEqual((report.cache_hits,report.live_requests,report.tokens_consumed),(1,0,0))

    def test_provider_failure_does_not_stop_next_item(self):
        a=make_purchase(self.settings,"B0TRACK001",score=90); b=make_purchase(self.settings,"B0TRACK002",score=80); self.save(a,b)
        provider=FakeProvider({a.asin:KeepaError("boom"),b.asin:result(b.asin)})
        report=self.service(provider).run(now=NOW)
        self.assertEqual((report.errors,report.processed,len(provider.calls)),(1,1,2))

    def test_tokens_exhausted_stops_remaining(self):
        a=make_purchase(self.settings,"B0TRACK001",score=90); b=make_purchase(self.settings,"B0TRACK002",score=80); self.save(a,b)
        exhausted=KeepaTokensExhausted(KeepaTokenMetadata(0,0,5,1000))
        provider=FakeProvider({a.asin:exhausted,b.asin:result(b.asin)})
        report=self.service(provider).run(now=NOW)
        self.assertTrue(report.stopped_tokens_exhausted); self.assertEqual(len(provider.calls),1)

    def test_observation_conversion(self):
        purchase=make_purchase(self.settings); observation=to_observation(purchase,result(purchase.asin,5500))
        self.assertEqual((observation.observed_price,observation.sales_rank,observation.new_offer_count,observation.amazon_owned,observation.data_quality),(5500,12000,4,False,"complete"))

    def test_observation_can_make_win(self):
        purchase=make_purchase(self.settings); self.save(purchase)
        report=self.service(FakeProvider({purchase.asin:result(purchase.asin,5500)})).run(now=NOW)
        self.assertEqual((report.observations_added,report.wins),(1,1))

    def test_thirty_days_can_make_loss(self):
        purchase=make_purchase(self.settings,age_days=30); self.save(purchase)
        report=self.service(FakeProvider({purchase.asin:result(purchase.asin,3500)})).run(now=NOW)
        self.assertEqual(report.losses,1)

    def test_missing_price_can_expire(self):
        purchase=make_purchase(self.settings,age_days=30); self.save(purchase)
        report=self.service(FakeProvider({purchase.asin:result(purchase.asin,None,quality=HistoryQuality.INSUFFICIENT)})).run(now=NOW)
        self.assertEqual(report.expired,1)

    def test_idempotent_observation_timestamp(self):
        configured=make_settings(self.db.path,virtual_purchase_track_interval_hours=0)
        purchase=make_purchase(configured); self.save(purchase)
        provider=FakeProvider({purchase.asin:result(purchase.asin,3500,observed_at=NOW.isoformat(),cache=True)})
        first=self.service(provider,settings=configured).run(now=NOW)
        second=self.service(provider,settings=configured).run(now=NOW)
        self.assertEqual((first.observations_added,second.observations_added),(1,0))
        with self.db.connect() as c: self.assertEqual(c.execute("SELECT COUNT(*) FROM virtual_purchase_observations").fetchone()[0],1)

    def test_entry_fee_snapshot_is_unchanged(self):
        purchase=make_purchase(self.settings); original=purchase.entry_snapshot; self.save(purchase)
        self.service(FakeProvider({purchase.asin:result(purchase.asin,3500)})).run(now=NOW)
        loaded=self.db.load_virtual_purchases()[0]
        self.assertEqual((loaded.entry_snapshot.fee_source,loaded.entry_snapshot.fee_model_version),(original.fee_source,original.fee_model_version))

    def test_v5_fee_snapshot_is_safely_baselined_when_loaded(self):
        purchase=make_purchase(self.settings); self.save(purchase)
        with self.db.connect() as c:
            snapshot=json.loads(c.execute("SELECT snapshot_json FROM virtual_purchases").fetchone()[0])
            for key in ("referral_rate","referral_fee","fulfillment_fee","shipping_cost","other_cost","total_fees","fee_source","fee_model_version","fee_calculated_at"):
                snapshot.pop(key,None)
            c.execute("UPDATE virtual_purchases SET snapshot_json=?,referral_fee=NULL,fulfillment_fee=NULL,total_fees=NULL",(json.dumps(snapshot),))
        loaded=self.db.load_virtual_purchases()[0]
        self.assertEqual((loaded.entry_snapshot.referral_rate,loaded.entry_snapshot.fulfillment_fee,loaded.entry_snapshot.fee_source),(0.10,450.0,"DEFAULT_ESTIMATE"))

    def test_dry_run_has_no_provider_or_database_updates(self):
        purchase=make_purchase(self.settings); self.save(purchase)
        provider=FakeProvider({purchase.asin:AssertionError("called")})
        report=self.service(provider).run(now=NOW,dry_run=True)
        self.assertEqual((report.planned_count,len(provider.calls)),(1,0))
        with self.db.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM virtual_purchase_observations").fetchone()[0],0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],0)

    def test_jobs_and_provider_errors_are_recorded(self):
        purchase=make_purchase(self.settings); self.save(purchase)
        self.service(FakeProvider({purchase.asin:KeepaError("safe failure")})).run(now=NOW)
        with self.db.connect() as c:
            self.assertEqual(c.execute("SELECT status FROM jobs").fetchone()[0],"COMPLETED")
            self.assertEqual(c.execute("SELECT provider FROM errors").fetchone()[0],"keepa")

    def test_cli_dry_run_does_not_call_keepa(self):
        with patch("app.config._load_dotenv"), patch.dict(os.environ,{"APP_DB_PATH":str(self.db.path)},clear=True), patch("app.providers.keepa.KeepaHttpClient.get_product",side_effect=AssertionError("API called")), redirect_stdout(io.StringIO()) as output:
            self.save(make_purchase(self.settings))
            self.assertEqual(main(["track-virtual-purchases","--dry-run"]),0)
        self.assertEqual(json.loads(output.getvalue())["live_requests"],0)

    def test_cli_mock_uses_no_live_keepa(self):
        path=Path(self.temp.name)/"cli-mock.db"
        with patch("app.config._load_dotenv"), patch.dict(os.environ,{"APP_DB_PATH":str(path)},clear=True), patch("app.providers.keepa.KeepaHttpClient.get_product",side_effect=AssertionError("API called")), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["track-virtual-purchases","--mode","mock"]),0)
        report=json.loads(output.getvalue()); self.assertEqual(report["live_requests"],0)
        with Database(path).connect() as c: self.assertEqual(c.execute("SELECT COUNT(*) FROM keepa_usage").fetchone()[0],0)


if __name__ == "__main__": unittest.main()
