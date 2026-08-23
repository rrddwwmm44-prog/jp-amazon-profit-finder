from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

class MissingState(StrEnum):
    UNKNOWN="unknown"; NOT_OBSERVED="not_observed"; PROVIDER_UNAVAILABLE="provider_unavailable"; NOT_APPLICABLE="not_applicable"; VERIFIED_ZERO="verified_zero"
class Verification(StrEnum): VERIFIED="VERIFIED"; PROVISIONAL="PROVISIONAL"

@dataclass
class ProductSignal:
    source: str; reason: str; product_name: str; manufacturer: str
    jan: str|None=None; asin: str|None=None; model: str|None=None
    amazon_price: float|None=None; source_price: float|None=None; shipping: float=0
    seller_count: int|None=None; sales_rank: int|None=None
    amazon_present: bool|None=None; amazon_owned: bool|None=None
    is_new: bool=False; is_renewal: bool=False; is_discontinued: bool=False
    price_drop_rate: float=0; amazon_rise_rate: float=0; strong_seller_entries: int=0
    source_url: str=""; amazon_url: str=""; observed_at: str=field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    evidence: dict[str, Any]=field(default_factory=dict)

@dataclass
class Candidate:
    signal: ProductSignal; score: int; confidence: int; verification: Verification
    profit_yen: float|None; margin: float|None; roi: float|None; status: str="ACTIVE"
