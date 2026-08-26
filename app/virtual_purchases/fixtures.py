from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.opportunities.aggregator import OpportunityAggregator
from app.opportunities.models import Signal
from app.virtual_purchases.models import FollowUpObservation, VirtualPurchase
from app.virtual_purchases.service import VirtualPurchaseService


BASE=datetime(2026,1,1,tzinfo=timezone.utc)


def mock_virtual_purchases(settings: Settings) -> list[VirtualPurchase]:
    service=VirtualPurchaseService(settings)
    cases=(
        ("B0VP000001",((7,5500),),30),
        ("B0VP000002",((7,3500),(14,5500)),30),
        ("B0VP000003",((7,3200),(30,3500)),30),
        ("B0VP000004",((30,None),),30),
        ("B0VP000005",((7,3500),),7),
    )
    results=[]
    for asin,observations,as_of_day in cases:
        opportunity=OpportunityAggregator().aggregate([_signal(asin)])[0]
        purchase=service.create(opportunity,created_at=BASE.isoformat())
        for day,price in observations:
            purchase=service.add_observation(purchase,FollowUpObservation(
                purchase.virtual_purchase_id,(BASE+timedelta(days=day)).isoformat(),price,
                sales_rank=12000,new_offer_count=4,amazon_owned=False,
                data_quality="complete" if price is not None else "insufficient",
            ))
        results.append(service.evaluate(purchase,as_of=(BASE+timedelta(days=as_of_day)).isoformat()))
    return results


def _signal(asin: str) -> Signal:
    return Signal(
        "amazon_arbitrage","amazon_arbitrage",asin,None,BASE.isoformat(),80,True,
        "mock profitable opportunity",{
            "purchase_price":3000,"expected_sale_price":5500,"profit_yen":1500,
            "roi":0.5,"sales_rank":12000,"new_offer_count":4,"amazon_owned":False,
            "median_price_30d":5500,"median_price_90d":5600,
        },confidence=85,quality="complete",product_name=f"virtual-{asin}",
    )
