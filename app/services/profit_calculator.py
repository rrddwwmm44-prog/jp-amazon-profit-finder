from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


class FeeSource(StrEnum):
    DEFAULT_ESTIMATE = "DEFAULT_ESTIMATE"
    MANUAL = "MANUAL"
    AMAZON_OFFICIAL = "AMAZON_OFFICIAL"
    SP_API = "SP_API"


class FulfillmentMethod(StrEnum):
    FBA = "FBA"
    FBM = "FBM"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class FeeModel:
    referral_rate: float = 0.10
    fulfillment_fee: float = 450
    shipping_cost: float = 0
    other_cost: float = 0
    fee_source: FeeSource = FeeSource.DEFAULT_ESTIMATE
    fee_model_version: str = "estimate_v1"
    calculated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self):
        if not 0 <= self.referral_rate <= 1: raise ValueError("referral_rate must be between 0 and 1")
        if any(value < 0 for value in (self.fulfillment_fee,self.shipping_cost,self.other_cost)):
            raise ValueError("fees and costs cannot be negative")


@dataclass(frozen=True)
class CalculationContext:
    asin: str | None = None
    category: str | None = None
    fulfillment_method: FulfillmentMethod = FulfillmentMethod.UNKNOWN
    sale_price: float | None = None
    purchase_price: float | None = None


@dataclass(frozen=True)
class ProfitResult:
    profit_yen: float
    margin: float
    roi: float
    total_fees: float
    total_cost: float
    referral_fee: float
    fulfillment_fee: float
    shipping_cost: float
    other_cost: float
    fee_source: FeeSource
    fee_model_version: str
    calculated_at: str


def calculate(
    sale_price: float, purchase_price: float, shipping: float = 0,
    referral_rate: float = .10, fulfillment_fee: float = 450,
    other_cost: float = 0, *, fee_model: FeeModel | None = None,
    context: CalculationContext | None = None,
) -> ProfitResult:
    """Calculate estimated profit while preserving the original call signature."""
    _ = context  # Descriptive only; unknown fields must not alter the calculation.
    model=fee_model or FeeModel(referral_rate,fulfillment_fee,shipping,other_cost)
    calculated_at=model.calculated_at or datetime.now(timezone.utc).isoformat()
    referral_fee=round(sale_price*model.referral_rate,2)
    total_fees=round(referral_fee+model.fulfillment_fee,2)
    total_cost=round(purchase_price+model.shipping_cost+model.other_cost+total_fees,2)
    profit=round(sale_price-total_cost,2)
    investment=purchase_price+model.shipping_cost
    return ProfitResult(
        profit,round(profit/sale_price,4) if sale_price else 0,
        round(profit/investment,4) if investment else 0,total_fees,total_cost,
        referral_fee,model.fulfillment_fee,model.shipping_cost,model.other_cost,
        model.fee_source,model.fee_model_version,calculated_at,
    )
