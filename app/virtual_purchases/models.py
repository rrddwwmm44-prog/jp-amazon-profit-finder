from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class VirtualPurchaseStatus(StrEnum):
    OPEN = "OPEN"
    WIN = "WIN"
    LOSS = "LOSS"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class EntrySnapshot:
    opportunity_observed_at: str
    entry_price: float
    expected_sale_price: float
    expected_profit_yen: float | None
    expected_roi: float | None
    opportunity_score: int
    urgency_score: int | None
    confidence: int | None
    signal_types: tuple[str, ...]
    sales_rank: int | None
    new_offer_count: int | None
    amazon_owned: bool | None
    median_price_30d: float | None
    median_price_90d: float | None
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    referral_rate: float
    referral_fee: float
    fulfillment_fee: float
    shipping_cost: float
    other_cost: float
    total_fees: float
    fee_source: str
    fee_model_version: str
    fee_calculated_at: str


@dataclass(frozen=True)
class FollowUpObservation:
    virtual_purchase_id: str
    observed_at: str
    observed_price: float | None
    sales_rank: int | None = None
    new_offer_count: int | None = None
    amazon_owned: bool | None = None
    data_quality: str | None = None


@dataclass(frozen=True)
class VirtualPurchaseOutcome:
    outcome_status: VirtualPurchaseStatus
    best_observed_price: float | None = None
    worst_observed_price: float | None = None
    max_potential_profit_yen: float | None = None
    max_potential_roi: float | None = None
    days_to_first_win: int | None = None
    evaluation_days: int = 0


@dataclass(frozen=True)
class VirtualPurchaseSummary:
    product_name: str | None
    asin: str | None
    entry_price: float
    latest_price: float | None
    expected_profit_yen: float | None
    current_potential_profit_yen: float | None
    opportunity_score: int
    signal_types: tuple[str, ...]
    days_elapsed: int
    status: VirtualPurchaseStatus
    max_potential_profit_yen: float | None


@dataclass(frozen=True)
class VirtualPurchase:
    virtual_purchase_id: str
    opportunity_id: str
    asin: str | None
    jan: str | None
    product_name: str | None
    created_at: str
    quantity: int
    status: VirtualPurchaseStatus
    entry_snapshot: EntrySnapshot
    observations: tuple[FollowUpObservation, ...]
    outcome: VirtualPurchaseOutcome
    summary: VirtualPurchaseSummary


@dataclass(frozen=True)
class VirtualPurchaseEligibility:
    eligible: bool
    reasons: tuple[str, ...] = ()
