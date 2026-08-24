from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from statistics import median
from typing import Any


KEEPA_EPOCH_OFFSET_MINUTES = 21_564_000
CSV_AMAZON = 0
CSV_MARKETPLACE_NEW = 1
CSV_SALES_RANK = 3
CSV_NEW_OFFER_COUNT = 11
CSV_BUY_BOX_SHIPPING = 18


class HistoryQuality(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


@dataclass(frozen=True)
class HistoryPoint:
    observed_at: datetime
    value: int


@dataclass(frozen=True)
class NormalizedKeepaHistory:
    asin: str
    title: str | None
    observed_at: str
    current_price: int | None
    price_7d: int | None
    price_30d: int | None
    price_90d: int | None
    median_price_30d: float | None
    median_price_90d: float | None
    current_sales_rank: int | None
    sales_rank_7d: int | None
    sales_rank_30d: int | None
    sales_rank_90d: int | None
    current_new_offer_count: int | None
    new_offer_count_7d: int | None
    new_offer_count_30d: int | None
    new_offer_count_90d: int | None
    amazon_price_current: int | None
    buy_box_price_current: int | None
    amazon_owned_current: bool | None
    quality: HistoryQuality


def keepa_time_to_datetime(keepa_minutes: int) -> datetime:
    return datetime.fromtimestamp((keepa_minutes + KEEPA_EPOCH_OFFSET_MINUTES) * 60, timezone.utc)


def decode_history(values: Any, *, stride: int = 2, allow_zero: bool = False) -> tuple[HistoryPoint, ...]:
    if not isinstance(values, list) or stride < 2:
        return ()
    points: list[HistoryPoint] = []
    for index in range(0, len(values) - stride + 1, stride):
        stamp, value = values[index], values[index + 1]
        if not isinstance(stamp, int) or not isinstance(value, int):
            continue
        if value < 0 or (value == 0 and not allow_zero):
            continue
        points.append(HistoryPoint(keepa_time_to_datetime(stamp), value))
    return tuple(sorted(points, key=lambda point: point.observed_at))


def point_in_time(points: tuple[HistoryPoint, ...], target: datetime, max_gap_days: int) -> int | None:
    eligible = [point for point in points if point.observed_at <= target]
    if not eligible:
        return None
    selected = eligible[-1]
    if target - selected.observed_at > timedelta(days=max_gap_days):
        return None
    return selected.value


def window_median(
    points: tuple[HistoryPoint, ...], observed_at: datetime, days: int, minimum_samples: int
) -> float | None:
    start = observed_at - timedelta(days=days)
    samples = [point.value for point in points if start <= point.observed_at <= observed_at]
    if len(samples) < minimum_samples:
        return None
    return float(median(samples))


def normalize_keepa_history(
    raw: dict[str, Any], *, observed_at: datetime | None = None, max_gap_days: int = 14,
    minimum_median_samples: int = 3,
) -> NormalizedKeepaHistory:
    observed_at = observed_at or datetime.now(timezone.utc)
    csv = raw.get("csv") if isinstance(raw.get("csv"), list) else []
    amazon = decode_history(_csv(csv, CSV_AMAZON))
    marketplace_new = decode_history(_csv(csv, CSV_MARKETPLACE_NEW))
    sales_rank = decode_history(_csv(csv, CSV_SALES_RANK))
    new_offers = decode_history(_csv(csv, CSV_NEW_OFFER_COUNT), allow_zero=True)
    buy_box = decode_history(_csv(csv, CSV_BUY_BOX_SHIPPING), stride=3)

    def at(points: tuple[HistoryPoint, ...], days: int = 0) -> int | None:
        return point_in_time(points, observed_at - timedelta(days=days), max_gap_days)

    current_price = at(marketplace_new)
    amazon_price = at(amazon)
    availability = raw.get("availabilityAmazon")
    if availability == -1:
        amazon_owned: bool | None = False
    elif availability in {0, 1, 3, 4}:
        amazon_owned = True
    elif availability == 2:
        amazon_owned = None
    else:
        amazon_owned = True if amazon_price is not None else None

    core = (
        current_price, at(marketplace_new, 7), at(marketplace_new, 30), at(marketplace_new, 90),
        at(sales_rank), at(sales_rank, 30), at(new_offers), at(new_offers, 30),
    )
    present = sum(value is not None for value in core)
    quality = HistoryQuality.COMPLETE if present == len(core) else HistoryQuality.PARTIAL if present else HistoryQuality.INSUFFICIENT
    return NormalizedKeepaHistory(
        asin=str(raw.get("asin") or "").upper(),
        title=raw.get("title") if isinstance(raw.get("title"), str) else None,
        observed_at=observed_at.isoformat(),
        current_price=current_price,
        price_7d=at(marketplace_new, 7), price_30d=at(marketplace_new, 30), price_90d=at(marketplace_new, 90),
        median_price_30d=window_median(marketplace_new, observed_at, 30, minimum_median_samples),
        median_price_90d=window_median(marketplace_new, observed_at, 90, minimum_median_samples),
        current_sales_rank=at(sales_rank), sales_rank_7d=at(sales_rank, 7),
        sales_rank_30d=at(sales_rank, 30), sales_rank_90d=at(sales_rank, 90),
        current_new_offer_count=at(new_offers), new_offer_count_7d=at(new_offers, 7),
        new_offer_count_30d=at(new_offers, 30), new_offer_count_90d=at(new_offers, 90),
        amazon_price_current=amazon_price, buy_box_price_current=at(buy_box),
        amazon_owned_current=amazon_owned, quality=quality,
    )


def _csv(csv: list[Any], index: int) -> Any:
    return csv[index] if len(csv) > index else None
