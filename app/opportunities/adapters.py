from __future__ import annotations

from app.opportunities.models import Signal
from app.services.amazon_arbitrage import ArbitrageAssessment
from app.services.seller_decline import SellerDeclineAssessment


def arbitrage_to_signal(assessment: ArbitrageAssessment, observed_at: str) -> Signal:
    item = assessment.item
    evidence = {
        **assessment.evidence,
        "median_price_30d": item.median_price_30d,
        "median_price_90d": item.median_price_90d,
        "marketplace_new_price": None,
    }
    return Signal(
        signal_type="amazon_arbitrage", source_engine="amazon_arbitrage",
        asin=item.asin or None, jan=None, observed_at=item.observed_at or observed_at,
        score=assessment.arbitrage_score, candidate=assessment.is_candidate,
        reason=assessment.reason, evidence=evidence, confidence=assessment.confidence,
        quality=item.demand_quality, product_name=item.title,
    )


def seller_decline_to_signal(assessment: SellerDeclineAssessment, observed_at: str) -> Signal:
    item = assessment.item
    evidence = {
        **assessment.evidence,
        "marketplace_new_price": item.price_current,
        "sales_rank": item.sales_rank_current,
        "new_offer_count": item.new_offers_current.value,
        "amazon_owned": item.amazon_owned,
    }
    return Signal(
        signal_type="seller_decline", source_engine="seller_decline",
        asin=item.asin or None, jan=None, observed_at=item.observed_at or observed_at,
        score=assessment.seller_decline_score, candidate=assessment.is_candidate,
        reason=assessment.reason, evidence=evidence,
        quality="partial" if assessment.is_provisional else "complete",
        product_name=item.title,
    )
