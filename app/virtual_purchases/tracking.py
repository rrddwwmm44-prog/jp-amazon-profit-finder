from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

from app.config import Settings
from app.providers.keepa import KeepaError, KeepaResult, KeepaTokensExhausted
from app.services.keepa_budget import build_keepa_budget
from app.storage.db import Database
from app.virtual_purchases.models import FollowUpObservation, VirtualPurchase, VirtualPurchaseStatus
from app.virtual_purchases.service import VirtualPurchaseService


class ProductDetailProvider(Protocol):
    def get_product(self, asin: str) -> KeepaResult: ...


@dataclass(frozen=True)
class TrackingCandidate:
    purchase: VirtualPurchase
    last_observed_at: str | None


@dataclass
class TrackingResult:
    eligible_open: int = 0
    due_count: int = 0
    budget_status: str = "UNKNOWN"
    planned_count: int = 0
    processed: int = 0
    cache_hits: int = 0
    live_requests: int = 0
    observations_added: int = 0
    wins: int = 0
    losses: int = 0
    expired: int = 0
    still_open: int = 0
    skipped: int = 0
    errors: int = 0
    tokens_consumed: int = 0
    tokens_left: int | None = None
    stopped_tokens_exhausted: bool = False
    candidates: tuple[dict, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


class VirtualPurchaseTrackingService:
    def __init__(self, settings: Settings, db: Database, provider: ProductDetailProvider | None = None,
                 budget_builder: Callable[[Database, datetime | None], dict] = build_keepa_budget):
        self.settings=settings
        self.db=db
        self.provider=provider
        self.budget_builder=budget_builder
        self.virtual_service=VirtualPurchaseService(settings)

    def select_due(self, purchases: list[VirtualPurchase], now: datetime) -> tuple[list[TrackingCandidate], int, int]:
        eligible=[item for item in purchases if item.status == VirtualPurchaseStatus.OPEN]
        due=[]; skipped=0
        interval=timedelta(hours=self.settings.virtual_purchase_track_interval_hours)
        for purchase in eligible:
            if not purchase.asin:
                skipped+=1; continue
            latest=max((parse(item.observed_at) for item in purchase.observations),default=None)
            if latest is not None and now-latest < interval:
                skipped+=1; continue
            due.append(TrackingCandidate(purchase,latest.isoformat() if latest else None))
        due.sort(key=lambda item:(
            -item.purchase.entry_snapshot.opportunity_score,
            -(item.purchase.entry_snapshot.urgency_score if item.purchase.entry_snapshot.urgency_score is not None else -1),
            parse(item.purchase.created_at),
            parse(item.last_observed_at) if item.last_observed_at else datetime.min.replace(tzinfo=timezone.utc),
            item.purchase.virtual_purchase_id,
        ))
        return due,len(eligible),skipped

    def plan_limit(self, budget: dict) -> int:
        status=budget.get("status","UNKNOWN")
        configured=max(0,self.settings.virtual_track_max_asins_per_run)
        status_limit={
            "HEALTHY":configured,
            "LIMITED":self.settings.virtual_track_limited_max_asins,
            "CRITICAL":self.settings.virtual_track_critical_max_asins,
            "EXHAUSTED":0,
            "UNKNOWN":self.settings.virtual_track_unknown_bootstrap_max_asins,
        }.get(status,0)
        allowed=min(configured,max(0,status_limit))
        tokens_left=budget.get("tokens_left")
        if isinstance(tokens_left,int): allowed=min(allowed,max(0,tokens_left))
        return allowed

    def run(self, *, now: datetime | None = None, dry_run: bool = False) -> TrackingResult:
        now=now or datetime.now(timezone.utc)
        purchases=self.db.load_virtual_purchases(VirtualPurchaseStatus.OPEN)
        due,eligible,skipped=self.select_due(purchases,now)
        budget=self.budget_builder(self.db,now)
        limit=self.plan_limit(budget)
        planned=due[:limit]
        result=TrackingResult(eligible_open=eligible,due_count=len(due),budget_status=budget["status"],
                              planned_count=len(planned),skipped=skipped+max(0,len(due)-len(planned)),
                              tokens_left=budget.get("tokens_left"),
                              candidates=tuple({"virtual_purchase_id":item.purchase.virtual_purchase_id,
                                                "asin":item.purchase.asin,"opportunity_score":item.purchase.entry_snapshot.opportunity_score,
                                                "urgency_score":item.purchase.entry_snapshot.urgency_score,
                                                "last_observed_at":item.last_observed_at} for item in planned))
        if dry_run or not planned:
            return result
        if self.provider is None:
            raise ValueError("Keepa provider is required")
        job_id=self.db.start_job("virtual_purchase_tracking")
        try:
            for candidate in planned:
                try:
                    keepa=self.provider.get_product(candidate.purchase.asin or "")
                    result.processed+=1
                    if keepa.cache_hit: result.cache_hits+=1
                    else:
                        result.live_requests+=1
                        if keepa.tokens and keepa.tokens.tokens_consumed is not None:
                            result.tokens_consumed+=keepa.tokens.tokens_consumed
                        if keepa.tokens and keepa.tokens.tokens_left is not None:
                            result.tokens_left=keepa.tokens.tokens_left
                    observation=to_observation(candidate.purchase,keepa)
                    updated=self.virtual_service.add_observation(candidate.purchase,observation)
                    before=len(candidate.purchase.observations)
                    updated=self.virtual_service.evaluate(updated,as_of=now.isoformat())
                    self.db.save_virtual_purchase(updated)
                    keepa_tokens=0 if keepa.cache_hit else (
                        keepa.tokens.tokens_consumed if keepa.tokens else None
                    )
                    self.db.record_virtual_purchase_tracking_cost(
                        candidate.purchase.virtual_purchase_id,observation.observed_at,keepa_tokens,
                    )
                    result.observations_added+=int(len(updated.observations)>before)
                    if updated.status == VirtualPurchaseStatus.WIN: result.wins+=1
                    elif updated.status == VirtualPurchaseStatus.LOSS: result.losses+=1
                    elif updated.status == VirtualPurchaseStatus.EXPIRED: result.expired+=1
                    else: result.still_open+=1
                    if result.tokens_left == 0:
                        result.stopped_tokens_exhausted=True; break
                except KeepaTokensExhausted as exc:
                    result.errors+=1; result.stopped_tokens_exhausted=True
                    if exc.tokens and exc.tokens.tokens_left is not None: result.tokens_left=exc.tokens.tokens_left
                    self.db.record_error(job_id,"keepa",exc); break
                except Exception as exc:
                    result.errors+=1; self.db.record_error(job_id,"keepa",exc)
            self.db.finish(job_id,"COMPLETED","done")
        except Exception as exc:
            self.db.record_error(job_id,"virtual_purchase_tracking",exc)
            self.db.finish(job_id,"FAILED",error=str(exc)); raise
        return result


def to_observation(purchase: VirtualPurchase, result: KeepaResult) -> FollowUpObservation:
    history=result.history
    return FollowUpObservation(
        purchase.virtual_purchase_id,
        history.observed_at if history else result.product.observed_at,
        history.current_price if history else None,
        history.current_sales_rank if history else result.product.sales_rank,
        history.current_new_offer_count if history else result.product.new_offer_count,
        history.amazon_owned_current if history else None,
        history.quality.value if history else "insufficient",
    )


def parse(value: str) -> datetime:
    parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
