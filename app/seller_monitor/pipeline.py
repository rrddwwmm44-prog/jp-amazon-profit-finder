from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Callable, Protocol

from app.config import Settings
from app.opportunities.adapters import arbitrage_to_signal, seller_decline_to_signal
from app.opportunities.aggregator import OpportunityAggregator
from app.opportunities.models import Signal
from app.providers.keepa import KeepaResult, KeepaTokensExhausted
from app.services.amazon_arbitrage import evaluate_arbitrage
from app.services.keepa_adapters import to_arbitrage_input, to_seller_decline_input
from app.services.keepa_budget import build_keepa_budget
from app.services.seller_decline import evaluate_seller_decline
from app.storage.db import Database
from app.virtual_purchases.service import VirtualPurchaseService


SELLER_MONITOR_SIGNAL_TYPE = "seller_monitor_new"
SELLER_MONITOR_STRATEGY_VERSION = "seller_monitor_v1"
SELLER_MONITOR_BASELINE_SCORE = 40
SELLER_MONITOR_BASELINE_CONFIDENCE = 50


class ProductDetailProvider(Protocol):
    def get_product(self, asin: str) -> KeepaResult: ...


@dataclass
class SellerDetectionProcessResult:
    detection_count: int = 0
    planned_count: int = 0
    processed_count: int = 0
    signals_created: int = 0
    opportunities_saved: int = 0
    virtual_purchases_created: int = 0
    skipped: int = 0
    duplicates: int = 0
    keepa_tokens: int = 0
    budget_status: str = "UNKNOWN"
    errors: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class SellerDetectionPipeline:
    def __init__(self, settings: Settings, db: Database, provider: ProductDetailProvider | None = None,
                 budget_builder: Callable = build_keepa_budget, max_detections: int = 5):
        self.settings=settings
        self.db=db
        self.provider=provider
        self.budget_builder=budget_builder
        self.max_detections=max(0,max_detections)
        self.virtual_service=VirtualPurchaseService(settings)

    def pending(self) -> list[dict]:
        with self.db.connect() as c:
            return [dict(row) for row in c.execute("""SELECT d.id,d.asin,d.source_type,d.seller_id,d.detected_at
                FROM seller_monitor_detections d
                JOIN seller_monitors s ON s.seller_id=d.seller_id
                WHERE d.processing_status IN ('PENDING','FAILED') AND s.enabled=1
                ORDER BY d.detected_at,d.id""")]

    def run(self, *, now: datetime | None = None, dry_run: bool = False) -> SellerDetectionProcessResult:
        now=now or datetime.now(timezone.utc)
        detections=self.pending()
        budget=self.budget_builder(self.db,now)
        result=SellerDetectionProcessResult(detection_count=len(detections),budget_status=budget.get("status","UNKNOWN"))
        limit=self._limit(budget,len(detections))
        result.planned_count=limit
        if dry_run or limit == 0:
            result.skipped=len(detections)
            return result
        if self.provider is None:
            raise ValueError("Keepa provider is required")
        for index,detection in enumerate(detections[:limit]):
            try:
                current_budget=self.budget_builder(self.db,now)
                if current_budget.get("status") in {"CRITICAL","EXHAUSTED"}:
                    result.skipped+=limit-index
                    break
                keepa=self.provider.get_product(detection["asin"])
                tokens=0 if keepa.cache_hit else keepa.tokens.tokens_consumed if keepa.tokens else None
                if tokens is not None:
                    result.keepa_tokens+=tokens
                if keepa.history is None:
                    self._mark(detection["id"],"PROCESSED",now,"missing_keepa_history")
                    result.processed_count+=1
                    result.skipped+=1
                    continue
                signals=self._signals(detection,keepa)
                result.signals_created+=sum(signal.candidate for signal in signals)
                opportunities=OpportunityAggregator().aggregate(signals)
                if not opportunities:
                    self._mark(detection["id"],"PROCESSED",now,None)
                    result.processed_count+=1
                    result.skipped+=1
                    continue
                opportunity=replace(
                    opportunities[0],source_type="seller_monitor",
                    source_id=detection["seller_id"],strategy_version=SELLER_MONITOR_STRATEGY_VERSION,
                )
                self.db.save_opportunity(opportunity)
                result.opportunities_saved+=1
                if self.db.has_virtual_purchase_for_opportunity(opportunity.opportunity_id):
                    result.duplicates+=1
                else:
                    eligibility=self.virtual_service.eligibility(opportunity)
                    if eligibility.eligible:
                        purchase=self.virtual_service.create(opportunity,created_at=detection["detected_at"])
                        self.db.save_virtual_purchase(purchase)
                        self.db.record_virtual_purchase_tracking_cost(
                            purchase.virtual_purchase_id,detection["detected_at"],tokens,
                        )
                        result.virtual_purchases_created+=1
                    else:
                        result.skipped+=1
                self._mark(detection["id"],"PROCESSED",now,None)
                result.processed_count+=1
            except KeepaTokensExhausted:
                result.errors+=1
                self._mark(detection["id"],"FAILED",now,"keepa_tokens_exhausted")
                result.skipped+=max(0,limit-index-1)
                break
            except Exception as exc:
                result.errors+=1
                self._mark(detection["id"],"FAILED",now,type(exc).__name__)
        result.skipped+=max(0,len(detections)-limit)
        return result

    def _signals(self, detection: dict, keepa: KeepaResult) -> list[Signal]:
        observed_at=detection["detected_at"]
        seller=Signal(
            SELLER_MONITOR_SIGNAL_TYPE,"seller_monitor",detection["asin"],None,observed_at,
            SELLER_MONITOR_BASELINE_SCORE,True,
            "seller storefront difference detected as NEW by this system",
            {"detection_id":detection["id"],"detected_at":observed_at,"actual_listing_time":"unknown"},
            confidence=SELLER_MONITOR_BASELINE_CONFIDENCE,quality="detection_only",
            product_name=keepa.product.title,source_type="seller_monitor",
            source_id=detection["seller_id"],strategy_version=SELLER_MONITOR_STRATEGY_VERSION,
        )
        arbitrage=evaluate_arbitrage(to_arbitrage_input(keepa.history),self.settings)
        decline=evaluate_seller_decline(to_seller_decline_input(keepa.history),self.settings)
        arbitrage_signal=replace(
            arbitrage_to_signal(arbitrage,observed_at),observed_at=observed_at,
            source_type="amazon_arbitrage",source_id="keepa",strategy_version="amazon_arbitrage_v1",
        )
        decline_signal=replace(
            seller_decline_to_signal(decline,observed_at),observed_at=observed_at,
            source_type="seller_decline",source_id="keepa",strategy_version="seller_decline_v1",
        )
        return [seller,arbitrage_signal,decline_signal]

    def _limit(self, budget: dict, count: int) -> int:
        status=budget.get("status","UNKNOWN")
        allowed=self.max_detections if status == "HEALTHY" else 1 if status in {"LIMITED","UNKNOWN"} else 0
        tokens_left=budget.get("tokens_left")
        if isinstance(tokens_left,int):
            allowed=min(allowed,max(0,tokens_left))
        return min(count,allowed)

    def _mark(self, detection_id: int, status: str, now: datetime, error: str | None) -> None:
        with self.db.connect() as c:
            c.execute("""UPDATE seller_monitor_detections
                SET processing_status=?,processed_at=?,processing_error=? WHERE id=?""",
                (status,now.isoformat(),error,detection_id))
