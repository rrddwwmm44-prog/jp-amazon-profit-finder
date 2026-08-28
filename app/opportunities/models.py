from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class OpportunityStatus(StrEnum):
    OPEN = "OPEN"


@dataclass(frozen=True)
class Signal:
    signal_type: str
    source_engine: str
    asin: str | None
    jan: str | None
    observed_at: str
    score: int
    candidate: bool
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    confidence: int | None = None
    quality: str | None = None
    urgency_hint: int | None = None
    product_name: str | None = None
    manufacturer: str | None = None
    source_type: str | None = None
    source_id: str | None = None
    strategy_version: str | None = None


@dataclass(frozen=True)
class OpportunitySummary:
    purchase_price: float | None = None
    expected_sale_price: float | None = None
    expected_profit_yen: float | None = None
    roi: float | None = None
    sales_rank: int | None = None
    new_offer_count: int | None = None
    amazon_owned: bool | None = None
    current_amazon_price: float | None = None
    marketplace_new_price: float | None = None
    median_price_30d: float | None = None
    median_price_90d: float | None = None
    history_quality: str | None = None
    signal_types: tuple[str, ...] = ()
    primary_reason: str | None = None


@dataclass(frozen=True)
class Opportunity:
    opportunity_id: str
    identity_type: str
    identity_value: str
    asin: str | None
    jan: str | None
    product_name: str | None
    manufacturer: str | None
    observed_at: str
    opportunity_score: int
    urgency_score: int | None
    confidence: int | None
    status: OpportunityStatus
    signal_count: int
    signals: tuple[Signal, ...]
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    evidence: tuple[dict[str, Any], ...]
    summary: OpportunitySummary
    source_type: str | None = None
    source_id: str | None = None
    strategy_version: str | None = None
