from __future__ import annotations

from datetime import datetime, timezone

from app.config import Settings
from app.domain import MissingState
from app.opportunities.adapters import arbitrage_to_signal, seller_decline_to_signal
from app.opportunities.models import Signal
from app.services.amazon_arbitrage import ArbitrageInput, evaluate_arbitrage
from app.services.seller_decline import SellerDeclineInput, SellerObservation, evaluate_seller_decline


def mock_signals(settings: Settings) -> list[Signal]:
    observed_at=datetime.now(timezone.utc).isoformat()
    asin="B0OPP00001"
    arbitrage=evaluate_arbitrage(ArbitrageInput(
        asin,"combined-product",3000,5500,5600,sales_rank=12000,
        new_offer_count=4,amazon_owned=False,demand_quality="good",observed_at=observed_at,
    ),settings)
    def seen(value: int): return SellerObservation(value,MissingState.VERIFIED_ZERO if value==0 else None)
    decline=evaluate_seller_decline(SellerDeclineInput(
        asin,"combined-product",seen(4),seen(8),seen(13),seen(18),
        5480,4800,4200,3980,26000,25000,False,observed_at,
    ),settings)
    return [arbitrage_to_signal(arbitrage,observed_at),seller_decline_to_signal(decline,observed_at)]
