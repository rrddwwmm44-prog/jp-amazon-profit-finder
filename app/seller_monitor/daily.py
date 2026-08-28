from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable

from app.config import Settings
from app.seller_monitor.pipeline import SellerDetectionPipeline
from app.seller_monitor.service import SellerMonitorService
from app.services.keepa_budget import build_keepa_budget
from app.storage.db import Database


@dataclass
class SellerMonitorDailyResult:
    started_at: str
    completed_at: str | None = None
    active_sellers: int = 0
    storefront_planned: int = 0
    storefront_checked: int = 0
    baseline_count: int = 0
    new_detection_count: int = 0
    processed_detection_count: int = 0
    signals_created: int = 0
    opportunities_saved: int = 0
    virtual_purchases_created: int = 0
    skipped: int = 0
    duplicates: int = 0
    errors: int = 0
    keepa_requests: int = 0
    tokens_consumed: int = 0
    tokens_left: int | None = None
    budget_status: str = "UNKNOWN"
    dry_run: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class SellerMonitorDailyService:
    def __init__(self, settings: Settings, db: Database, provider=None,
                 budget_builder: Callable = build_keepa_budget):
        self.settings=settings
        self.db=db
        self.provider=provider
        self.budget_builder=budget_builder

    def run(self, *, now: datetime | None = None, dry_run: bool = False) -> SellerMonitorDailyResult:
        now=now or datetime.now(timezone.utc)
        result=SellerMonitorDailyResult(now.isoformat(),dry_run=dry_run)
        usage_id=self._latest_usage_id()
        job_id=None
        try:
            monitor=SellerMonitorService(
                self.db,self.provider,self.budget_builder,
                max_sellers_per_run=self.settings.seller_monitor_daily_max_sellers,
            )
            active=[item for item in monitor.list_sellers() if item["enabled"]]
            result.active_sellers=len(active)
            if dry_run:
                budget=self.budget_builder(self.db,now)
                result.budget_status=budget.get("status","UNKNOWN")
                result.tokens_left=budget.get("tokens_left")
                result.storefront_planned=self._storefront_limit(result.active_sellers,budget)
                pipeline=SellerDetectionPipeline(self.settings,self.db,None,self.budget_builder)
                process=pipeline.run(now=now,dry_run=True)
                result.processed_detection_count=process.processed_count
                result.skipped=process.skipped
                result.completed_at=datetime.now(timezone.utc).isoformat()
                return result
            if self.provider is None:
                raise ValueError("Keepa provider is required")
            job_id=self.db.start_job("seller_monitor_daily")
            checks=monitor.check_enabled(now=now)
            result.storefront_planned=checks["planned"]
            result.storefront_checked=checks["checked"]
            result.baseline_count=sum(
                item["current_asin_count"] for item in checks["results"]
                if item["observation_type"]=="BASELINE"
            )
            result.new_detection_count=sum(item["new_count"] for item in checks["results"])
            result.errors=len(checks["errors"])
            for error in checks["errors"]:
                self.db.record_error(job_id,f"seller:{error['seller_id']}",RuntimeError(error["error_class"]))
            process=SellerDetectionPipeline(
                self.settings,self.db,self.provider,self.budget_builder,
                max_detections=self.settings.seller_monitor_daily_max_detections,
            ).run(now=now)
            result.processed_detection_count=process.processed_count
            result.signals_created=process.signals_created
            result.opportunities_saved=process.opportunities_saved
            result.virtual_purchases_created=process.virtual_purchases_created
            result.skipped=process.skipped
            result.duplicates=process.duplicates
            result.errors+=process.errors
            self.db.finish(job_id,"COMPLETED","done")
        except Exception as exc:
            result.errors+=1
            if job_id is not None:
                self.db.record_error(job_id,"seller_monitor_daily",exc)
                self.db.finish(job_id,"FAILED",error=type(exc).__name__)
            raise
        finally:
            usage=self._usage_after(usage_id)
            result.keepa_requests=len(usage)
            result.tokens_consumed=sum(item["tokens_consumed"] for item in usage if item["tokens_consumed"] is not None)
            budget=self.budget_builder(self.db,datetime.now(timezone.utc))
            result.budget_status=budget.get("status","UNKNOWN")
            result.tokens_left=budget.get("tokens_left")
            result.completed_at=datetime.now(timezone.utc).isoformat()
        return result

    def _latest_usage_id(self) -> int:
        with self.db.connect() as c:
            row=c.execute("SELECT COALESCE(MAX(id),0) FROM keepa_usage").fetchone()
        return int(row[0])

    def _usage_after(self, usage_id: int) -> list[dict]:
        with self.db.connect() as c:
            return [dict(row) for row in c.execute("SELECT * FROM keepa_usage WHERE id>? ORDER BY id",(usage_id,))]

    @staticmethod
    def _storefront_limit(active: int, budget: dict) -> int:
        status=budget.get("status","UNKNOWN")
        limit=active if status == "HEALTHY" else 1 if status in {"LIMITED","UNKNOWN"} else 0
        tokens_left=budget.get("tokens_left")
        if isinstance(tokens_left,int): limit=min(limit,max(0,tokens_left//10))
        return limit
