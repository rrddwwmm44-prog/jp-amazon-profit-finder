from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.virtual_purchases.models import VirtualPurchaseStatus


class SampleQuality(StrEnum):
    INSUFFICIENT = "INSUFFICIENT"
    EARLY = "EARLY"
    USABLE = "USABLE"


@dataclass(frozen=True)
class PerformanceSample:
    virtual_purchase_id: str
    signal_types: tuple[str, ...]
    fee_source: str
    fee_model_version: str
    status: VirtualPurchaseStatus
    opportunity_score: int
    confidence: int | None
    history_quality: str | None
    amazon_owned: bool | None
    sales_rank: int | None
    new_offer_count: int | None
    max_potential_profit_yen: float | None
    max_potential_roi: float | None
    days_to_first_win: int | None

    @property
    def strategy_key(self) -> str:
        return "+".join(sorted(set(self.signal_types)))


@dataclass(frozen=True)
class PerformanceMetrics:
    total_count: int
    closed_count: int
    win_count: int
    loss_count: int
    open_count: int
    expired_count: int
    win_rate: float | None
    average_max_potential_profit_yen: float | None
    median_max_potential_profit_yen: float | None
    average_max_potential_roi: float | None
    median_max_potential_roi: float | None
    average_days_to_win: float | None
    median_days_to_win: float | None
    sample_quality: SampleQuality
    virtual_purchase_ids: tuple[str, ...]


@dataclass(frozen=True)
class StrategyPerformance:
    strategy_key: str
    signal_types: tuple[str, ...]
    fee_source: str
    fee_model_version: str
    metrics: PerformanceMetrics


@dataclass(frozen=True)
class BucketPerformance:
    bucket_key: str
    metrics: PerformanceMetrics


@dataclass(frozen=True)
class StrategyPerformanceReport:
    fee_source: str
    fee_model_version: str
    overall: PerformanceMetrics
    strategies: tuple[StrategyPerformance, ...]
    score_buckets: tuple[BucketPerformance, ...]
    signal_count_buckets: tuple[BucketPerformance, ...]
    amazon_owned_buckets: tuple[BucketPerformance, ...]
    sales_rank_buckets: tuple[BucketPerformance, ...]
    new_offer_count_buckets: tuple[BucketPerformance, ...]
    confidence_buckets: tuple[BucketPerformance, ...]
    history_quality_buckets: tuple[BucketPerformance, ...]
