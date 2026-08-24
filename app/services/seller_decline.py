from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.config import Settings
from app.domain import MissingState


class DemandTrend(StrEnum):
    IMPROVING="improving"
    MAINTAINED="maintained"
    WORSENING="worsening"
    UNKNOWN="unknown"


@dataclass(frozen=True)
class SellerObservation:
    value: int | None
    state: MissingState | None = None

    def __post_init__(self):
        if self.value is None and self.state not in {MissingState.UNKNOWN,MissingState.NOT_OBSERVED,MissingState.PROVIDER_UNAVAILABLE}:
            raise ValueError("missing seller value requires a missing state")
        if self.value == 0 and self.state != MissingState.VERIFIED_ZERO:
            raise ValueError("zero seller value must be verified_zero")
        if self.value is not None and self.value < 0:
            raise ValueError("seller value cannot be negative")
        if self.value not in {None,0} and self.state == MissingState.VERIFIED_ZERO:
            raise ValueError("verified_zero requires zero")


@dataclass(frozen=True)
class SellerDeclineInput:
    asin: str
    title: str
    new_offers_current: SellerObservation
    new_offers_7d: SellerObservation
    new_offers_30d: SellerObservation
    new_offers_90d: SellerObservation
    price_current: float | None
    price_7d: float | None
    price_30d: float | None
    price_90d: float | None
    sales_rank_current: int | None
    sales_rank_30d: int | None
    amazon_owned: bool | None
    observed_at: str | None = None


@dataclass(frozen=True)
class SellerDeclineAssessment:
    item: SellerDeclineInput
    is_candidate: bool
    is_provisional: bool
    decline_rates: dict[str,float | None]
    decline_acceleration: float | None
    price_trends: dict[str,float | None]
    demand_trend: DemandTrend
    supply_contraction_likely: bool
    demand_decline_risk: bool
    seller_decline_score: int
    reject_reasons: tuple[str,...]
    reason: str
    evidence: dict


def _rate(current: int | None,past: int | None) -> float | None:
    if current is None or past is None or past <= 0:
        return None
    return round((past-current)/past,4)


def _price_rate(current: float | None,past: float | None) -> float | None:
    if current is None or past is None or past <= 0:
        return None
    return round((current-past)/past,4)


def _demand(current: int | None,past: int | None) -> DemandTrend:
    if current is None or past is None or past <= 0:
        return DemandTrend.UNKNOWN
    ratio=current/past
    if ratio <= 0.80: return DemandTrend.IMPROVING
    if ratio <= 1.20: return DemandTrend.MAINTAINED
    return DemandTrend.WORSENING


def _acceleration(item: SellerDeclineInput) -> float | None:
    current=item.new_offers_current.value; d7=item.new_offers_7d.value; d30=item.new_offers_30d.value; d90=item.new_offers_90d.value
    if current is None or d7 is None:
        return None
    recent=(d7-current)/7
    earlier=[]
    if d30 is not None: earlier.append((d30-d7)/23)
    if d90 is not None and d30 is not None: earlier.append((d90-d30)/60)
    if not earlier:
        return None
    return round(recent-sum(earlier)/len(earlier),4)


def evaluate_seller_decline(item: SellerDeclineInput,settings: Settings) -> SellerDeclineAssessment:
    current=item.new_offers_current.value
    rates={"7d":_rate(current,item.new_offers_7d.value),"30d":_rate(current,item.new_offers_30d.value),"90d":_rate(current,item.new_offers_90d.value)}
    prices={"7d":_price_rate(item.price_current,item.price_7d),"30d":_price_rate(item.price_current,item.price_30d),"90d":_price_rate(item.price_current,item.price_90d)}
    acceleration=_acceleration(item)
    demand=_demand(item.sales_rank_current,item.sales_rank_30d)
    points=[item.new_offers_90d.value,item.new_offers_30d.value,item.new_offers_7d.value,current]
    observed_pairs=[(a,b) for a,b in zip(points,points[1:]) if a is not None and b is not None]
    declining_intervals=sum(a>b for a,b in observed_pairs)
    sufficient_history=rates["30d"] is not None and len(observed_pairs)>=2
    continuous=declining_intervals>=2
    demand_risk=demand == DemandTrend.WORSENING and prices["30d"] is not None and prices["30d"] < 0
    contraction=bool(sufficient_history and continuous and rates["30d"] >= settings.min_seller_decline_rate_30d and not demand_risk and (demand in {DemandTrend.IMPROVING,DemandTrend.MAINTAINED} or (prices["30d"] is not None and prices["30d"] > 0)))
    score=_score(rates["30d"],acceleration,prices["30d"],demand,item.amazon_owned)
    rejects=[]
    if not sufficient_history: rejects.append("insufficient_history")
    if rates["30d"] is not None and (rates["30d"] < settings.min_seller_decline_rate_30d or not continuous): rejects.append("insufficient_seller_decline")
    if demand_risk: rejects.append("demand_decline_risk")
    if score < settings.min_seller_decline_score: rejects.append("weak_score")
    provisional=not sufficient_history or demand == DemandTrend.UNKNOWN
    evidence={"new_offer_count_current":current,"new_offer_count_current_state":item.new_offers_current.state.value if item.new_offers_current.state else "observed","decline_rates":rates,"decline_acceleration":acceleration,"declining_intervals":declining_intervals,"price_trends":prices,"demand_trend":demand.value,"amazon_owned":item.amazon_owned,"supply_contraction_likely":contraction}
    reason=_reason(evidence)
    return SellerDeclineAssessment(item,not rejects,provisional,rates,acceleration,prices,demand,contraction,demand_risk,score,tuple(rejects),reason,evidence)


def _score(decline_30d: float | None,acceleration: float | None,price_30d: float | None,demand: DemandTrend,amazon_owned: bool | None) -> int:
    score=0.0
    if decline_30d is not None: score+=min(35,max(0,decline_30d)/0.60*35)
    if acceleration is not None: score+=min(15,max(0,acceleration)/0.50*15)
    if price_30d is not None and price_30d > 0: score+=min(20,price_30d/0.30*20)
    score+={DemandTrend.IMPROVING:20,DemandTrend.MAINTAINED:15,DemandTrend.UNKNOWN:7,DemandTrend.WORSENING:0}[demand]
    score+=10 if amazon_owned is False else 4 if amazon_owned is None else 0
    return max(0,min(100,round(score)))


def _reason(evidence: dict) -> str:
    def pct(value): return "unknown" if value is None else f"{value:.1%}"
    return "; ".join((f"seller_30d_decline={pct(evidence['decline_rates']['30d'])}",f"seller_90d_decline={pct(evidence['decline_rates']['90d'])}",f"acceleration={evidence['decline_acceleration'] if evidence['decline_acceleration'] is not None else 'unknown'}",f"price_30d={pct(evidence['price_trends']['30d'])}",f"demand={evidence['demand_trend']}",f"amazon_owned={evidence['amazon_owned']}"))
