import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.providers.keepa import (
    JAPAN_DOMAIN_ID, KeepaProvider, KeepaTokensExhausted, MockKeepaClient,
)
from app.storage.db import Database


ASIN = "B012345678"


class KeepaProviderTests(unittest.TestCase):
    def test_mock_product_is_normalized_for_japan(self):
        result = KeepaProvider("test-key", MockKeepaClient()).get_product(ASIN)
        self.assertEqual((result.product.asin, result.product.domain_id, result.product.marketplace), (ASIN, JAPAN_DOMAIN_ID, "amazon.co.jp"))
        self.assertEqual((result.product.amazon_price, result.product.sales_rank, result.product.new_offer_count), (5980, 12345, 7))

    def test_token_metadata_is_normalized(self):
        tokens = KeepaProvider("test-key", MockKeepaClient()).get_product(ASIN).tokens
        self.assertEqual((tokens.tokens_left, tokens.tokens_consumed, tokens.refill_rate, tokens.refill_in), (42, 1, 5, 30000))
        self.assertEqual((tokens.token_flow_reduction, tokens.processing_time_ms), (0.0, 12))

    def test_missing_values_are_not_converted_to_zero(self):
        product = KeepaProvider("test-key", MockKeepaClient(missing=True)).get_product(ASIN).product
        self.assertIsNone(product.title)
        self.assertIsNone(product.amazon_price)
        self.assertIsNone(product.buy_box_price)
        self.assertIsNone(product.sales_rank)
        self.assertIsNone(product.new_offer_count)

    def test_429_is_identified_as_token_exhaustion(self):
        with self.assertRaises(KeepaTokensExhausted) as raised:
            KeepaProvider("test-key", MockKeepaClient(exhausted=True)).get_product(ASIN)
        self.assertEqual(raised.exception.code, "keepa_tokens_exhausted")
        self.assertEqual(raised.exception.tokens.tokens_left, 0)
        self.assertNotIn("test-key", str(raised.exception))

    def test_cache_prevents_duplicate_client_call(self):
        with tempfile.TemporaryDirectory() as raw:
            db = Database(Path(raw) / "cache.db")
            db.migrate()
            client = MockKeepaClient()
            provider = KeepaProvider("test-key", client, db, 3600)
            first = provider.get_product(ASIN)
            second = provider.get_product(ASIN)
            self.assertFalse(first.cache_hit)
            self.assertTrue(second.cache_hit)
            self.assertIsNone(second.tokens)
            self.assertEqual(client.calls, 1)

    def test_api_key_is_required_only_when_keepa_provider_is_used(self):
        with self.assertRaises(ValueError):
            KeepaProvider("")
        with tempfile.TemporaryDirectory() as raw, patch.dict(os.environ, {}, clear=True):
            old = os.getcwd()
            try:
                os.chdir(raw)
                settings = Settings.load()
            finally:
                os.chdir(old)
        self.assertFalse(settings.keepa_enabled)


if __name__ == "__main__":
    unittest.main()
