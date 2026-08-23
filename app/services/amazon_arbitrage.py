from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.domain import ProductSignal
from app.services.confidence import assess
from app.services.profit_calculator import ProfitResult, calculate


@dataclass(frozen=True)
class ArbitrageInput:
    asin: str
    title: str
    purchase_price: float | None
    median_price_30d: float | None = None
    median_price_90d: float | None = None
    average_price: float | None = None
    historical_low: float | None = None
    historical_high: float | None = None
    buy_box_price: float | None = None
    sales_rank: int | None = None
    new_seller_count: int | None = None
    amazon_owned: bool | None = None
    demand_quality: str | None = None
    observed_at: str | None = None


@dataclass(frozen=True)
class ArbitrageAssessment:
    item: ArbitrageInput
    is_candidate: bool
    expected_sale_price: float | None
    absolute_drop_yen: float | None
    drop_rate: float | None
    profit: ProfitResult | None
    arbitrage_score: int
    confidence: int
    reject_reasons: tuple[str, ...]
    reason: str
    evidence: dict


def expected_sale_price(item: ArbitrageInput) -> float | None:
    medians=[value for value in (item.median_price_30d,item.median_price_90d) if value is not None and value > 0]
    if not medians or item.purchase_price is None:
        return None
    conservative=min(medians)
    return conservative if conservative > item.purchase_price else None


def evaluate_arbitrage(item: ArbitrageInput, settings: Settings) -> ArbitrageAssessment:
    expected=expected_sale_price(item)
    purchase=item.purchase_price
    absolute_drop=round(expected-purchase,2) if expected is not None and purchase is not None else None
    drop_rate=round(absolute_drop/expected,4) if absolute_drop is not None and expected else None
    profit=calculate(expected,purchase) if expected is not None and purchase is not None else None
    rejects=[]
    if expected is None: rejects.append("missing_expected_sale_price")
    if profit is not None and profit.profit_yen < settings.min_arbitrage_profit_yen: rejects.append("insufficient_profit")
    if profit is not None and profit.roi < settings.min_arbitrage_roi: rejects.append("insufficient_roi")
    if drop_rate is not None and drop_rate < settings.min_arbitrage_drop_rate: rejects.append("insufficient_price_drop")
    weak_demand=item.demand_quality == "weak" or (item.sales_rank is not None and item.sales_rank > settings.max_arbitrage_sales_rank)
    if weak_demand: rejects.append("weak_demand")
    if item.new_seller_count is not None and item.new_seller_count > settings.max_arbitrage_seller_count: rejects.append("excessive_competition")
    score=_score(item,drop_rate,profit,settings)
    missing=[]
    if item.sales_rank is None: missing.append("sales_rank")
    if item.new_seller_count is None: missing.append("new_seller_count")
    signal=ProductSignal("keepa_fixture","amazon_arbitrage",item.title,"",asin=item.asin,sales_rank=item.sales_rank,seller_count=item.new_seller_count,amazon_owned=item.amazon_owned,evidence={"matching_sources":["keepa"],"missing":missing})
    confidence,_=assess(signal)
    evidence={
        "purchase_price":purchase,"expected_sale_price":expected,"absolute_drop_yen":absolute_drop,
        "drop_rate":drop_rate,"profit_yen":profit.profit_yen if profit else None,
        "roi":profit.roi if profit else None,"sales_rank":item.sales_rank,
        "new_seller_count":item.new_seller_count,"amazon_owned":item.amazon_owned,
        "amazon_owned_return_risk":item.amazon_owned is True,"missing":missing,
    }
    reason=_reason(evidence)
    return ArbitrageAssessment(item,not rejects,expected,absolute_drop,drop_rate,profit,score,confidence,tuple(rejects),reason,evidence)


def _score(item: ArbitrageInput, drop_rate: float | None, profit: ProfitResult | None, settings: Settings) -> int:
    score=0.0
    if drop_rate is not None: score+=min(30,drop_rate/0.40*30)
    if profit is not None:
        score+=min(25,max(0,profit.roi)/0.50*25)
        score+=min(20,max(0,profit.profit_yen)/3000*20)
    if item.demand_quality == "good" or (item.sales_rank is not None and item.sales_rank <= 50000): score+=15
    elif item.sales_rank is not None and item.sales_rank <= settings.max_arbitrage_sales_rank: score+=8
    elif item.sales_rank is None: score+=4
    if item.new_seller_count is None: score+=3
    elif item.new_seller_count <= 5: score+=10
    elif item.new_seller_count <= settings.max_arbitrage_seller_count: score+=5
    if item.amazon_owned is True: score-=10
    return max(0,min(100,round(score)))


def _reason(evidence: dict) -> str:
    parts=[]
    if evidence["drop_rate"] is not None: parts.append(f"price_drop={evidence['drop_rate']:.1%}")
    if evidence["profit_yen"] is not None: parts.append(f"profit={evidence['profit_yen']:.0f}yen")
    if evidence["roi"] is not None: parts.append(f"roi={evidence['roi']:.1%}")
    parts.append(f"sales_rank={evidence['sales_rank'] if evidence['sales_rank'] is not None else 'unknown'}")
    parts.append(f"sellers={evidence['new_seller_count'] if evidence['new_seller_count'] is not None else 'unknown'}")
    if evidence["amazon_owned_return_risk"]: parts.append("amazon_owned_return_risk")
    return "; ".join(parts)
