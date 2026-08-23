from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from app.storage.db import Database


CRITICAL_TOKENS_LEFT = 5
LIMITED_TOKENS_LEFT = 20
MIN_REQUIRED_EVENTS = 2
MIN_OBSERVATION_SECONDS = 60


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _window(db: Database, now: datetime, hours: int) -> dict:
    cutoff=(now-timedelta(hours=hours)).isoformat()
    usage=db.keepa_usage_since(cutoff)
    hits=db.keepa_cache_hits_since(cutoff)
    known=[row["tokens_consumed"] for row in usage if row["tokens_consumed"] is not None]
    total=sum(known)
    denominator=len(usage)+len(hits)
    operations={}
    for operation in sorted({row["operation"] for row in usage+hits}):
        operation_usage=[row for row in usage if row["operation"] == operation]
        operation_hits=sum(row["operation"] == operation for row in hits)
        operations[operation]={
            "requests":len(operation_usage),
            "tokens_consumed":sum(row["tokens_consumed"] for row in operation_usage if row["tokens_consumed"] is not None),
            "cache_hits":operation_hits,
        }
    return {
        "requests": len(usage), "tokens_consumed": total,
        "average_tokens_per_request": round(total/len(known),3) if known else None,
        "cache_hits": len(hits),
        "cache_hit_rate": round(len(hits)/denominator,4) if denominator else None,
        "operations":operations,
    }


def _required(rows: list[dict]) -> dict:
    known=[row for row in rows if row["tokens_consumed"] is not None]
    if len(known) < MIN_REQUIRED_EVENTS:
        return {"status":"insufficient_data","average_required_tokens_per_min":None,"peak_required_tokens_per_min":None}
    start,end=_parse(known[0]["observed_at"]),_parse(known[-1]["observed_at"])
    seconds=(end-start).total_seconds()
    if seconds < MIN_OBSERVATION_SECONDS:
        return {"status":"insufficient_data","average_required_tokens_per_min":None,"peak_required_tokens_per_min":None}
    minutes=max(1.0,seconds/60)
    per_minute=defaultdict(int)
    for row in known:
        at=_parse(row["observed_at"]).replace(second=0,microsecond=0)
        per_minute[at]+=row["tokens_consumed"]
    return {
        "status":"measured",
        "average_required_tokens_per_min":round(sum(row["tokens_consumed"] for row in known)/minutes,3),
        "peak_required_tokens_per_min":max(per_minute.values()),
    }


def _round_up_five(value: float) -> int:
    return int(math.ceil(value/5)*5)


def _external(latest: list[dict]) -> float | None:
    if len(latest) < 2:
        return None
    current,previous=latest[0],latest[1]
    required=(current["tokens_left"],previous["tokens_left"],current["refill_rate"],previous["refill_rate"],current["tokens_consumed"])
    if any(value is None for value in required):
        return None
    elapsed=(_parse(current["observed_at"])-_parse(previous["observed_at"])).total_seconds()
    if elapsed <= 0 or elapsed > 3600 or current["refill_rate"] != previous["refill_rate"]:
        return None
    current_reduction=current["token_flow_reduction"] or 0
    previous_reduction=previous["token_flow_reduction"] or 0
    if current_reduction != previous_reduction:
        return None
    capacity=max(0.0,current["refill_rate"]-current_reduction)
    bucket_capacity=capacity*60
    expected=min(previous["tokens_left"]+(elapsed/60)*capacity,bucket_capacity)
    estimate=expected-current["tokens_left"]-current["tokens_consumed"]
    return round(max(0.0,estimate),3)


def build_keepa_budget(db: Database, now: datetime | None = None) -> dict:
    now=now or datetime.now(timezone.utc)
    recent=db.keepa_usage_since((now-timedelta(days=7)).isoformat())
    latest=db.latest_keepa_usage(2)
    requirements=_required(recent)
    last=latest[0] if latest else None
    refill=last["refill_rate"] if last else None
    reduction=(last["token_flow_reduction"] or 0) if last else None
    capacity=max(0.0,refill-reduction) if refill is not None else None
    average=requirements["average_required_tokens_per_min"]
    peak=requirements["peak_required_tokens_per_min"]
    avg_shortage=max(0.0,average-capacity) if average is not None and capacity is not None else None
    peak_shortage=max(0.0,peak-capacity) if peak is not None and capacity is not None else None
    if last is None or last["tokens_left"] is None or capacity is None:
        status="UNKNOWN"
    elif last["tokens_left"] <= 0:
        status="EXHAUSTED"
    elif last["tokens_left"] <= CRITICAL_TOKENS_LEFT or (avg_shortage is not None and avg_shortage > 0):
        status="CRITICAL"
    elif last["tokens_left"] <= LIMITED_TOKENS_LEFT or (peak_shortage is not None and peak_shortage > 0):
        status="LIMITED"
    else:
        status="HEALTHY"
    if average is None or peak is None:
        minimum=comfortable=None
    else:
        minimum=_round_up_five(max(average*1.2,peak))
        comfortable=_round_up_five(max(minimum*1.25,peak*1.2))
    return {
        "status":status,
        "tokens_left":last["tokens_left"] if last else None,
        "refill_rate_tokens_per_min":refill,
        "current_capacity_tokens_per_min":capacity,
        "windows":{"last_1h":_window(db,now,1),"last_24h":_window(db,now,24),"last_7d":_window(db,now,24*7)},
        "required":requirements,
        "shortage":{"average":round(avg_shortage,3) if avg_shortage is not None else None,"peak":round(peak_shortage,3) if peak_shortage is not None else None},
        "recommended":{"minimum_tokens_per_min":minimum,"comfortable_tokens_per_min":comfortable},
        "estimated_external_consumption":_external(latest),
    }
