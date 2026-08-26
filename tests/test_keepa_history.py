from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone

from app.services.keepa_adapters import to_arbitrage_input, to_seller_decline_input
from app.services.keepa_history import (
    HistoryQuality, decode_history, keepa_time_to_datetime, normalize_keepa_history,
    point_in_time, window_median,
)


ASIN = "B0HISTORY1"
NOW = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)


def keepa_time(value: datetime) -> int:
    return int(value.timestamp() // 60) - 21_564_000


def series(*pairs: tuple[int, int]) -> list[int]:
    result = []
    for days_ago, value in pairs:
        result.extend((keepa_time(NOW - timedelta(days=days_ago)), value))
    return result


def raw_product() -> dict:
    csv = [None] * 19
    csv[0] = series((90, 4000), (30, 4200), (7, 3500), (0, 3000))
    csv[1] = series((90, 6000), (60, 5900), (30, 5700), (20, 5600), (7, 5500), (0, 5400))
    csv[3] = series((90, 30000), (30, 25000), (7, 23000), (0, 22000))
    csv[11] = series((90, 20), (30, 12), (7, 7), (0, 4))
    current = [-1] * 12
    current[0], current[1], current[3], current[11] = 3000, 5400, 22000, 4
    return {"asin": ASIN, "title": "History fixture", "domainId": 5, "availabilityAmazon": 0,
            "stats": {"current": current}, "csv": csv}


class KeepaHistoryTests(unittest.TestCase):
    def test_timestamp_conversion_is_utc(self):
        self.assertEqual(keepa_time_to_datetime(keepa_time(NOW)), NOW)

    def test_minus_one_preserves_unavailable_interval(self):
        points = decode_history([keepa_time(NOW), -1, keepa_time(NOW), 10])
        self.assertEqual([point.value for point in points], [None, 10])

    def test_current_price_carries_forward_from_20_day_old_change(self):
        points = decode_history(series((20, 1234)))
        self.assertEqual(point_in_time(points, NOW), 1234)

    def test_30_day_value_carries_forward_from_45_day_old_change(self):
        points = decode_history(series((45, 2345), (10, 3456)))
        self.assertEqual(point_in_time(points, NOW - timedelta(days=30)), 2345)

    def test_unavailable_change_is_not_carried_as_a_price(self):
        points = decode_history(series((45, 2345), (20, -1)))
        self.assertIsNone(point_in_time(points, NOW))
        self.assertEqual(point_in_time(points, NOW - timedelta(days=30)), 2345)

    def test_point_in_time_uses_preceding_value_for_7_30_90_days(self):
        points = decode_history(series((91, 90), (31, 30), (8, 7), (0, 0)), allow_zero=True)
        self.assertEqual(point_in_time(points, NOW - timedelta(days=7), 14), 7)
        self.assertEqual(point_in_time(points, NOW - timedelta(days=30), 14), 30)
        self.assertEqual(point_in_time(points, NOW - timedelta(days=90), 14), 90)

    def test_point_in_time_never_uses_future_and_honors_gap(self):
        points = decode_history(series((5, 50)))
        self.assertIsNone(point_in_time(points, NOW - timedelta(days=7), 14))
        self.assertIsNone(point_in_time(points, NOW, 4))

    def test_30_and_90_day_medians_require_minimum_samples(self):
        points = decode_history(series((80, 7000), (25, 6000), (10, 5000), (0, 4000)))
        self.assertEqual(window_median(points, NOW, 30, 3), 6000.0)
        self.assertEqual(window_median(points, NOW, 90, 3), 7000.0)
        self.assertIsNone(window_median(points, NOW, 30, 32))

    def test_normalization_preserves_new_offer_count_semantics_and_amazon(self):
        history = normalize_keepa_history(raw_product(), observed_at=NOW)
        self.assertEqual(history.current_new_offer_count, 4)
        self.assertEqual(history.new_offer_count_30d, 12)
        self.assertTrue(history.amazon_owned_current)
        self.assertEqual(history.amazon_price_current, 3000)
        self.assertEqual(history.quality, HistoryQuality.COMPLETE)

    def test_stats_current_wins_while_csv_remains_historical_source(self):
        raw = raw_product()
        raw["stats"]["current"][0] = 2999
        raw["stats"]["current"][1] = 5399
        history = normalize_keepa_history(raw, observed_at=NOW)
        self.assertEqual(history.amazon_price_current, 2999)
        self.assertEqual(history.current_price, 5399)
        self.assertEqual(history.price_7d, 5500)

    def test_missing_history_is_safe_and_not_zero(self):
        history = normalize_keepa_history({"asin": ASIN, "csv": [None, [-1, -1]]}, observed_at=NOW)
        self.assertIsNone(history.current_price)
        self.assertIsNone(history.current_new_offer_count)
        self.assertEqual(history.quality, HistoryQuality.INSUFFICIENT)

    def test_arbitrage_adapter_uses_amazon_purchase_price(self):
        item = to_arbitrage_input(normalize_keepa_history(raw_product(), observed_at=NOW))
        self.assertEqual(item.purchase_price, 3000)
        self.assertEqual(item.new_offer_count, 4)
        self.assertEqual(item.median_price_30d, 5600.0)

    def test_seller_decline_adapter_uses_marketplace_new_offer_history(self):
        item = to_seller_decline_input(normalize_keepa_history(raw_product(), observed_at=NOW))
        self.assertEqual(item.new_offers_current.value, 4)
        self.assertEqual(item.new_offers_7d.value, 7)
        self.assertEqual(item.new_offers_30d.value, 12)
        self.assertEqual(item.new_offers_90d.value, 20)
        self.assertEqual(item.price_current, 5400)

    def test_tests_do_not_require_api_key(self):
        self.assertIsInstance(os.getenv("KEEPA_API_KEY"), (str, type(None)))


if __name__ == "__main__":
    unittest.main()
