from __future__ import annotations

from app.strategy_performance.models import PerformanceSample
from app.virtual_purchases.models import VirtualPurchaseStatus


def mock_performance_samples() -> list[PerformanceSample]:
    rows=(
        (("amazon_arbitrage",),"WIN",95,True,5000,2,1800,.60,7),
        (("amazon_arbitrage",),"LOSS",85,False,30000,6,-300,-.10,None),
        (("amazon_arbitrage",),"OPEN",75,None,90000,10,200,.06,None),
        (("amazon_arbitrage",),"EXPIRED",65,None,None,None,None,None,None),
        (("seller_decline",),"WIN",82,False,25000,4,1400,.42,14),
        (("seller_decline",),"LOSS",68,True,160000,18,-500,-.15,None),
        (("amazon_arbitrage","seller_decline"),"WIN",100,False,8000,3,2400,.80,7),
        (("seller_decline","amazon_arbitrage"),"LOSS",92,True,60000,9,-200,-.07,None),
        (("amazon_arbitrage",),"WIN",78,False,45000,7,1100,.31,21),
        (("seller_decline",),"LOSS",72,None,None,None,None,None,None),
        (("amazon_arbitrage","seller_decline"),"WIN",88,False,12000,5,1700,.50,14),
        (("amazon_arbitrage",),"LOSS",58,True,200000,20,-800,-.25,None),
    )
    return [PerformanceSample(
        f"vp-{index:02d}",tuple(signals),"DEFAULT_ESTIMATE","estimate_v1",
        VirtualPurchaseStatus(status),score,85 if index%3 else None,
        "complete" if index%4 else None,owned,rank,offers,profit,roi,days,
    ) for index,(signals,status,score,owned,rank,offers,profit,roi,days) in enumerate(rows,1)]
