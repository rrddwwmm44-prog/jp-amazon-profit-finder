import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.providers.keepa import KeepaSellerResult, KeepaTokenMetadata
from app.seller_monitor.service import SellerMonitorService
from app.storage.db import Database


SELLER_ID = "A12345678901"
NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)


def asins(count):
    return tuple(f"B{i:09d}" for i in range(count))


class FakeSellerProvider:
    def __init__(self, observations):
        self.observations = iter(observations)
        self.calls = 0

    def get_seller_storefront(self, seller_id):
        self.calls += 1
        values = next(self.observations)
        return KeepaSellerResult(seller_id, "Fixture Seller", values, KeepaTokenMetadata(100, 10, 5))


class SellerMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "seller.db")
        self.db.migrate()

    def tearDown(self):
        self.temp.cleanup()

    def service(self, observations=()):
        return SellerMonitorService(self.db, FakeSellerProvider(observations), lambda db, now: {"status": "HEALTHY"})

    def test_first_500_are_baseline_then_one_new_from_501(self):
        service = self.service((asins(500), asins(501)))
        service.add(SELLER_ID, "Registered Name", "memo")
        first = service.check(SELLER_ID, now=NOW)
        second = service.check(SELLER_ID, now=NOW.replace(hour=1))
        self.assertEqual((first.observation_type, first.current_asin_count, first.new_count), ("BASELINE", 500, 0))
        self.assertEqual((second.observation_type, second.current_asin_count, second.new_count), ("CHECK", 501, 1))
        with self.db.connect() as c:
            counts = dict(c.execute("SELECT status,COUNT(*) FROM seller_monitor_asins GROUP BY status"))
            row = c.execute("SELECT asin,source_type,seller_id,detected_at FROM seller_monitor_detections").fetchone()
        self.assertEqual(counts, {"BASELINE": 500, "NEW": 1})
        self.assertEqual(tuple(row)[:3], ("B000000500", "seller_monitor", SELLER_ID))
        self.assertEqual(len(service.list_new(SELLER_ID)), 1)
        monitor = service.get(SELLER_ID)
        self.assertEqual((monitor["current_asin_count"], monitor["last_new_count"], monitor["last_checked_at"]), (501, 1, second.checked_at))

    def test_duplicate_asins_are_stored_once(self):
        values = ("B000000001", "B000000001", "b000000002")
        service = self.service((values,))
        service.add(SELLER_ID)
        result = service.check(SELLER_ID, now=NOW)
        self.assertEqual((result.current_asin_count, result.new_count), (2, 0))

    def test_disabled_seller_is_not_called(self):
        provider = FakeSellerProvider((asins(1),))
        service = SellerMonitorService(self.db, provider, lambda db, now: {"status": "HEALTHY"})
        service.add(SELLER_ID)
        service.set_enabled(SELLER_ID, False)
        report = service.check_enabled(now=NOW)
        self.assertEqual((report["checked"], provider.calls), (0, 0))
        with self.assertRaisesRegex(ValueError, "disabled"):
            service.check(SELLER_ID, now=NOW)

    def test_exhausted_budget_does_not_call_keepa(self):
        provider = FakeSellerProvider((asins(1),))
        service = SellerMonitorService(self.db, provider, lambda db, now: {"status": "EXHAUSTED", "tokens_left": 0})
        service.add(SELLER_ID)
        report = service.check_enabled(now=NOW)
        self.assertEqual((report["planned"], report["checked"], provider.calls), (0, 0, 0))

    def test_single_check_respects_budget(self):
        provider = FakeSellerProvider((asins(1),))
        service = SellerMonitorService(self.db, provider, lambda db, now: {"status": "CRITICAL", "tokens_left": 5})
        service.add(SELLER_ID)
        with self.assertRaisesRegex(ValueError, "budget"):
            service.check(SELLER_ID, now=NOW)
        self.assertEqual(provider.calls, 0)


if __name__ == "__main__":
    unittest.main()
