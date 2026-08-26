from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256

from app.config import Settings
from app.opportunities.models import Opportunity, OpportunityStatus
from app.services.profit_calculator import calculate
from app.virtual_purchases.models import (
    EntrySnapshot, FollowUpObservation, VirtualPurchase, VirtualPurchaseEligibility,
    VirtualPurchaseOutcome, VirtualPurchaseStatus, VirtualPurchaseSummary,
)


class VirtualPurchaseService:
    def __init__(self, settings: Settings, evaluation_days: int = 30):
        self.settings=settings
        self.evaluation_days=evaluation_days

    def eligibility(self, opportunity: Opportunity) -> VirtualPurchaseEligibility:
        reasons=[]
        if opportunity.status != OpportunityStatus.OPEN: reasons.append("opportunity_not_open")
        if opportunity.summary.purchase_price is None: reasons.append("missing_purchase_price")
        if opportunity.summary.expected_sale_price is None: reasons.append("missing_expected_sale_price")
        return VirtualPurchaseEligibility(not reasons,tuple(reasons))

    def create(self, opportunity: Opportunity, *, created_at: str | None = None, quantity: int = 1) -> VirtualPurchase:
        eligibility=self.eligibility(opportunity)
        if not eligibility.eligible: raise ValueError(",".join(eligibility.reasons))
        if quantity < 1: raise ValueError("quantity_must_be_positive")
        created_at=created_at or datetime.now(timezone.utc).isoformat()
        summary=opportunity.summary
        snapshot=EntrySnapshot(
            opportunity.observed_at,float(summary.purchase_price),float(summary.expected_sale_price),
            summary.expected_profit_yen,summary.roi,opportunity.opportunity_score,
            opportunity.urgency_score,opportunity.confidence,summary.signal_types,
            summary.sales_rank,summary.new_offer_count,summary.amazon_owned,
            summary.median_price_30d,summary.median_price_90d,opportunity.reasons,opportunity.risks,
        )
        identity=f"{opportunity.opportunity_id}:{opportunity.observed_at}"
        virtual_id=sha256(identity.encode()).hexdigest()[:24]
        outcome=VirtualPurchaseOutcome(VirtualPurchaseStatus.OPEN,evaluation_days=0)
        frontend=VirtualPurchaseSummary(
            opportunity.product_name,opportunity.asin,snapshot.entry_price,None,
            snapshot.expected_profit_yen,None,snapshot.opportunity_score,snapshot.signal_types,
            0,VirtualPurchaseStatus.OPEN,None,
        )
        return VirtualPurchase(virtual_id,opportunity.opportunity_id,opportunity.asin,opportunity.jan,
                               opportunity.product_name,created_at,quantity,VirtualPurchaseStatus.OPEN,
                               snapshot,(),outcome,frontend)

    def add_observation(self, purchase: VirtualPurchase, observation: FollowUpObservation) -> VirtualPurchase:
        if observation.virtual_purchase_id != purchase.virtual_purchase_id:
            raise ValueError("observation_virtual_purchase_mismatch")
        if _parse(observation.observed_at) < _parse(purchase.created_at):
            raise ValueError("observation_precedes_entry")
        if any(item.observed_at == observation.observed_at for item in purchase.observations):
            return purchase
        return replace(purchase,observations=tuple(sorted((*purchase.observations,observation),key=lambda item:item.observed_at)))

    def evaluate(self, purchase: VirtualPurchase, *, as_of: str | None = None) -> VirtualPurchase:
        as_of=as_of or datetime.now(timezone.utc).isoformat()
        elapsed=max(0,(_parse(as_of)-_parse(purchase.created_at)).days)
        priced=[item for item in purchase.observations if item.observed_price is not None and item.observed_price > 0]
        calculations=[(item,calculate(item.observed_price,purchase.entry_snapshot.entry_price)) for item in priced]
        wins=[(item,result) for item,result in calculations if result.profit_yen >= self.settings.min_arbitrage_profit_yen and result.roi >= self.settings.min_arbitrage_roi]
        if wins: status=VirtualPurchaseStatus.WIN
        elif elapsed < self.evaluation_days: status=VirtualPurchaseStatus.OPEN
        elif priced: status=VirtualPurchaseStatus.LOSS
        else: status=VirtualPurchaseStatus.EXPIRED
        best=max((item.observed_price for item in priced),default=None)
        worst=min((item.observed_price for item in priced),default=None)
        max_result=max((result for _,result in calculations),key=lambda result:result.profit_yen,default=None)
        first_win=min((_parse(item.observed_at) for item,_ in wins),default=None)
        days_to_win=(_parse(first_win.isoformat())-_parse(purchase.created_at)).days if first_win else None
        outcome=VirtualPurchaseOutcome(
            status,best,worst,max_result.profit_yen*purchase.quantity if max_result else None,
            max_result.roi if max_result else None,days_to_win,elapsed,
        )
        latest=max(purchase.observations,key=lambda item:item.observed_at,default=None)
        latest_calc=calculate(latest.observed_price,purchase.entry_snapshot.entry_price) if latest and latest.observed_price is not None and latest.observed_price > 0 else None
        summary=VirtualPurchaseSummary(
            purchase.product_name,purchase.asin,purchase.entry_snapshot.entry_price,
            latest.observed_price if latest else None,purchase.entry_snapshot.expected_profit_yen,
            latest_calc.profit_yen*purchase.quantity if latest_calc else None,
            purchase.entry_snapshot.opportunity_score,purchase.entry_snapshot.signal_types,
            elapsed,status,outcome.max_potential_profit_yen,
        )
        return replace(purchase,status=status,outcome=outcome,summary=summary)


def _parse(value: str) -> datetime:
    parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
