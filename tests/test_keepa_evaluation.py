from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.cli import main
from app.config import Settings
from app.providers.keepa import KeepaProvider
from app.services.keepa_evaluation import evaluate_keepa_asin
from app.storage.db import Database


ASIN = "B0LIVE0001"
KEEPA_OFFSET = 21_564_000


def keepa_time(at: datetime) -> int:
    return int(at.timestamp() // 60) - KEEPA_OFFSET


def series(now: datetime, pairs: tuple[tuple[int, int], ...]) -> list[int]:
    values=[]
    for days,value in pairs:
        values.extend((keepa_time(now-timedelta(days=days)),value))
    return values


class HistoryClient:
    def __init__(self, *, insufficient: bool = False):
        self.calls=0; self.insufficient=insufficient

    def get_product(self,api_key,asin,domain_id):
        self.calls+=1
        now=datetime.now(timezone.utc)
        csv=[None]*19
        if not self.insufficient:
            csv[0]=series(now,((90,4000),(30,4200),(7,3500),(0,3000)))
            csv[1]=series(now,((90,6000),(30,5700),(20,5600),(7,5500),(0,5400)))
            csv[3]=series(now,((90,30000),(30,25000),(7,23000),(0,22000)))
            csv[11]=series(now,((90,20),(30,12),(7,7),(0,4)))
        current=[-1]*12; current[0]=3000; current[3]=22000; current[11]=4
        return {
            "tokensLeft":42,"tokensConsumed":1,"refillRate":5,"refillIn":30000,
            "products":[{"asin":asin,"domainId":domain_id,"title":"Live flow fixture","availabilityAmazon":0,"stats":{"current":current},"csv":csv}],
        }


class KeepaEvaluationTests(unittest.TestCase):
    def settings(self,path):
        return Settings(path,500,.15,85,"INFO")

    def test_mock_history_runs_both_engine_evaluations_and_token_output(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"eval.db"; db=Database(path); db.migrate(); settings=self.settings(path)
            report=evaluate_keepa_asin(KeepaProvider("test-key",HistoryClient(),db),ASIN,settings,db)
            self.assertIn("candidate",report["amazon_arbitrage"])
            self.assertIn("candidate",report["seller_decline"])
            self.assertEqual(report["current"]["new_offer_count"],4)
            self.assertEqual(report["tokens"]["tokens_consumed"],1)
            self.assertEqual((report["tokens"]["tokens_left"],report["tokens"]["refill_rate"]),(42,5))

    def test_cache_hit_does_not_call_client_again(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"cache.db"; db=Database(path); db.migrate(); settings=self.settings(path); client=HistoryClient()
            provider=KeepaProvider("test-key",client,db)
            evaluate_keepa_asin(provider,ASIN,settings,db)
            second=evaluate_keepa_asin(provider,ASIN,settings,db)
            self.assertEqual(client.calls,1)
            self.assertTrue(second["keepa"]["cache_hit"])
            self.assertEqual(second["tokens"]["tokens_consumed"],0)

    def test_user_output_never_calls_count_new_seller_count(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"terms.db"; db=Database(path); db.migrate()
            report=evaluate_keepa_asin(KeepaProvider("test-key",HistoryClient(),db),ASIN,self.settings(path),db)
            self.assertNotIn("seller_count",str(report))
            self.assertIn("new_offer_count",str(report))

    def test_insufficient_history_is_safe(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"missing.db"; db=Database(path); db.migrate()
            report=evaluate_keepa_asin(KeepaProvider("test-key",HistoryClient(insufficient=True),db),ASIN,self.settings(path),db)
            self.assertEqual(report["history"]["quality"],"insufficient")
            self.assertFalse(report["amazon_arbitrage"]["candidate"])
            self.assertFalse(report["seller_decline"]["candidate"])

    def test_cli_rejects_missing_key_without_network(self):
        with tempfile.TemporaryDirectory() as raw, patch("app.config._load_dotenv"), patch.dict(os.environ,{"APP_DB_PATH":str(Path(raw)/"no-key.db")},clear=True), redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main(["keepa-evaluate",ASIN]),2)
            self.assertIn("not configured",error.getvalue())

    def test_cli_validates_single_asin_before_transport(self):
        with tempfile.TemporaryDirectory() as raw, patch.dict(os.environ,{"APP_DB_PATH":str(Path(raw)/"invalid.db"),"KEEPA_API_KEY":"test-key"},clear=True), redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main(["keepa-evaluate","INVALID"]),2)
            self.assertIn("invalid ASIN",error.getvalue())


if __name__ == "__main__":
    unittest.main()
