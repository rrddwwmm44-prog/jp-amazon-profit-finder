from __future__ import annotations

from dataclasses import dataclass


LEGACY_SOURCE_TYPE = "legacy"
UNKNOWN_SOURCE_ID = "unknown"
LEGACY_STRATEGY_VERSION = "legacy"
CURRENT_EVALUATION_RULE_VERSION = "vp_eval_v1"
CURRENT_MEASUREMENT_WINDOW_VERSION = "vp_window_v1"


@dataclass(frozen=True)
class ComparisonContract:
    source_type: str
    source_id: str
    strategy_version: str
    evaluation_rule_version: str
    measurement_window_version: str
    fee_model_version: str


@dataclass(frozen=True)
class TrackingCost:
    virtual_purchase_id: str
    observed_at: str
    keepa_tokens: int | None = None
    api_calls: int | None = None
    ai_calls: int | None = None
    manual_review_count: int | None = None
