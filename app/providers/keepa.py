from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.storage.db import Database
from app.services.keepa_history import HistoryQuality, NormalizedKeepaHistory, normalize_keepa_history


KEEPA_PRODUCT_URL = "https://api.keepa.com/product"
JAPAN_DOMAIN_ID = 5
JAPAN_MARKETPLACE = "amazon.co.jp"


@dataclass(frozen=True)
class KeepaTokenMetadata:
    tokens_left: int | None = None
    tokens_consumed: int | None = None
    refill_rate: int | None = None
    refill_in: int | None = None
    token_flow_reduction: float | None = None
    processing_time_ms: int | None = None


@dataclass(frozen=True)
class KeepaProduct:
    asin: str
    domain_id: int
    marketplace: str
    title: str | None
    amazon_price: int | None
    buy_box_price: int | None
    sales_rank: int | None
    new_offer_count: int | None
    observed_at: str


@dataclass(frozen=True)
class KeepaResult:
    product: KeepaProduct
    tokens: KeepaTokenMetadata | None
    cache_hit: bool = False
    history: NormalizedKeepaHistory | None = None


class KeepaError(RuntimeError):
    code = "keepa_error"


class KeepaTokensExhausted(KeepaError):
    code = "keepa_tokens_exhausted"

    def __init__(self, tokens: KeepaTokenMetadata | None = None):
        self.tokens = tokens
        super().__init__(self.code)


class KeepaHttpError(KeepaError):
    def __init__(self, status: int | None, payload: dict[str, Any] | None = None):
        self.status = status
        self.payload = payload or {}
        super().__init__(f"keepa_http_error status={status}")


class KeepaTransport(Protocol):
    def get_product(self, api_key: str, asin: str, domain_id: int) -> dict[str, Any]: ...


class KeepaHttpClient:
    """Minimal Product Request client. Errors never include the key or request URL."""

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    def get_product(self, api_key: str, asin: str, domain_id: int) -> dict[str, Any]:
        params = urlencode({"key": api_key, "domain": domain_id, "asin": asin, "stats": 1})
        request = Request(
            f"{KEEPA_PRODUCT_URL}?{params}",
            headers={"Accept": "application/json", "Accept-Encoding": "gzip", "User-Agent": "jp-amazon-profit-finder/0.1"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                payload = {}
            raise KeepaHttpError(exc.code, payload) from None
        except (URLError, TimeoutError, json.JSONDecodeError):
            raise KeepaHttpError(None) from None


class MockKeepaClient:
    def __init__(self, *, missing: bool = False, exhausted: bool = False):
        self.missing = missing
        self.exhausted = exhausted
        self.calls = 0

    def get_product(self, api_key: str, asin: str, domain_id: int) -> dict[str, Any]:
        self.calls += 1
        envelope = {"tokensLeft": 42, "tokensConsumed": 1, "refillRate": 5, "refillIn": 30000, "tokenFlowReduction": 0.0, "processingTimeInMs": 12}
        if self.exhausted:
            envelope["tokensLeft"] = 0
            envelope["tokensConsumed"] = 0
            raise KeepaHttpError(429, envelope)
        current = [-1] * 12
        if not self.missing:
            current[0], current[3], current[11] = 5980, 12345, 7
        envelope["products"] = [{"asin": asin, "domainId": domain_id, "title": None if self.missing else "Mock Keepa Product", "stats": {"current": current}}]
        return envelope


class KeepaProvider:
    """Candidate-ASIN detail provider; intentionally separate from market Provider.fetch()."""

    def __init__(self, api_key: str, client: KeepaTransport | None = None, db: Database | None = None,
                 cache_ttl_seconds: int = 21600, history_max_gap_days: int = 14,
                 history_minimum_median_samples: int = 3):
        if not api_key:
            raise ValueError("Keepa API key is not configured")
        self._api_key = api_key
        self._client = client or KeepaHttpClient()
        self._db = db
        self._cache_ttl_seconds = cache_ttl_seconds
        self._history_max_gap_days = history_max_gap_days
        self._history_minimum_median_samples = history_minimum_median_samples

    def get_product(self, asin: str) -> KeepaResult:
        asin = asin.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{10}", asin):
            raise ValueError("invalid ASIN")
        if self._db is not None and self._cache_ttl_seconds > 0:
            cached = self._db.get_keepa_cache(asin, JAPAN_MARKETPLACE, self._cache_ttl_seconds)
            if cached:
                payload, _ = cached
                if "product" in payload:
                    product = KeepaProduct(**payload["product"])
                    history_payload = payload.get("history")
                    history = NormalizedKeepaHistory(
                        **{**history_payload, "quality": HistoryQuality(history_payload["quality"])}
                    ) if isinstance(history_payload, dict) else None
                    self._db.record_keepa_cache_hit(datetime.now(timezone.utc).isoformat(), "product", asin)
                    return KeepaResult(product, None, True, history)
                # Legacy flat cache entries contain no history and are refreshed once.
        observed_at = datetime.now(timezone.utc).isoformat()
        try:
            payload = self._client.get_product(self._api_key, asin, JAPAN_DOMAIN_ID)
        except KeepaHttpError as exc:
            tokens = _tokens(exc.payload) if exc.payload else None
            if self._db is not None:
                self._db.record_keepa_usage(observed_at, "product", asin, tokens, "exhausted" if exc.status == 429 else "failed")
            if exc.status == 429:
                raise KeepaTokensExhausted(tokens) from None
            raise
        tokens = _tokens(payload)
        status = "failed" if payload.get("error") or not (payload.get("products") or []) else "success"
        if self._db is not None:
            self._db.record_keepa_usage(observed_at, "product", asin, tokens, status)
        if payload.get("error"):
            raise KeepaError("Keepa returned an error")
        products = payload.get("products") or []
        if not products:
            raise KeepaError("Keepa returned no product")
        raw_product = products[0]
        product = _normalize_product(raw_product, asin)
        history = normalize_keepa_history(
            raw_product, observed_at=datetime.fromisoformat(product.observed_at),
            max_gap_days=self._history_max_gap_days,
            minimum_median_samples=self._history_minimum_median_samples,
        )
        if self._db is not None:
            self._db.save_keepa_cache(
                asin, JAPAN_MARKETPLACE, product.observed_at,
                {"product": asdict(product), "history": asdict(history)},
            )
        return KeepaResult(product, tokens, history=history)


def _value(values: Any, index: int) -> int | None:
    if not isinstance(values, list) or len(values) <= index:
        return None
    value = values[index]
    return value if isinstance(value, int) and value >= 0 else None


def _normalize_product(raw: dict[str, Any], requested_asin: str) -> KeepaProduct:
    domain_id = raw.get("domainId")
    if domain_id != JAPAN_DOMAIN_ID:
        raise KeepaError("Keepa product marketplace mismatch")
    stats = raw.get("stats") if isinstance(raw.get("stats"), dict) else {}
    current = stats.get("current")
    buy_box = stats.get("buyBoxPrice")
    if not isinstance(buy_box, int) or buy_box < 0:
        buy_box = None
    return KeepaProduct(
        asin=str(raw.get("asin") or requested_asin).upper(), domain_id=domain_id,
        marketplace=JAPAN_MARKETPLACE, title=raw.get("title") if isinstance(raw.get("title"), str) else None,
        amazon_price=_value(current, 0), buy_box_price=buy_box,
        sales_rank=_value(current, 3), new_offer_count=_value(current, 11),
        observed_at=datetime.now(timezone.utc).isoformat(),
    )


def _tokens(payload: dict[str, Any]) -> KeepaTokenMetadata:
    return KeepaTokenMetadata(
        tokens_left=payload.get("tokensLeft"), tokens_consumed=payload.get("tokensConsumed"),
        refill_rate=payload.get("refillRate"), refill_in=payload.get("refillIn"),
        token_flow_reduction=payload.get("tokenFlowReduction"), processing_time_ms=payload.get("processingTimeInMs"),
    )
