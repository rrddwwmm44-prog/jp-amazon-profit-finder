from __future__ import annotations

from app.domain import MissingState
from app.services.amazon_arbitrage import ArbitrageInput
from app.services.keepa_history import NormalizedKeepaHistory
from app.services.seller_decline import SellerDeclineInput, SellerObservation


def to_arbitrage_input(history: NormalizedKeepaHistory) -> ArbitrageInput:
    return ArbitrageInput(
        asin=history.asin, title=history.title or history.asin,
        purchase_price=history.amazon_price_current,
        median_price_30d=history.median_price_30d, median_price_90d=history.median_price_90d,
        buy_box_price=history.buy_box_price_current, sales_rank=history.current_sales_rank,
        new_offer_count=history.current_new_offer_count, amazon_owned=history.amazon_owned_current,
        observed_at=history.observed_at,
    )


def to_seller_decline_input(history: NormalizedKeepaHistory) -> SellerDeclineInput:
    return SellerDeclineInput(
        asin=history.asin, title=history.title or history.asin,
        new_offers_current=_observation(history.current_new_offer_count),
        new_offers_7d=_observation(history.new_offer_count_7d),
        new_offers_30d=_observation(history.new_offer_count_30d),
        new_offers_90d=_observation(history.new_offer_count_90d),
        price_current=history.current_price, price_7d=history.price_7d,
        price_30d=history.price_30d, price_90d=history.price_90d,
        sales_rank_current=history.current_sales_rank, sales_rank_30d=history.sales_rank_30d,
        amazon_owned=history.amazon_owned_current, observed_at=history.observed_at,
    )


def _observation(value: int | None) -> SellerObservation:
    if value is None:
        return SellerObservation(None, MissingState.NOT_OBSERVED)
    if value == 0:
        return SellerObservation(0, MissingState.VERIFIED_ZERO)
    return SellerObservation(value)
