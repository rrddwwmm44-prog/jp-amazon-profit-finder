from __future__ import annotations

import json
from collections import defaultdict
from statistics import mean, median

from app.storage.db import Database
from app.strategy_performance.models import (
    BucketPerformance, PerformanceMetrics, PerformanceSample, SampleQuality,
    StrategyPerformance, StrategyPerformanceReport,
)
from app.virtual_purchases.models import VirtualPurchase, VirtualPurchaseStatus


class StrategyPerformanceService:
    def sample_from_purchase(self, purchase: VirtualPurchase) -> PerformanceSample:
        entry=purchase.entry_snapshot
        return PerformanceSample(
            purchase.virtual_purchase_id,tuple(sorted(set(entry.signal_types))),entry.fee_source,
            entry.fee_model_version,purchase.status,entry.opportunity_score,entry.confidence,None,
            entry.amazon_owned,entry.sales_rank,entry.new_offer_count,
            purchase.outcome.max_potential_profit_yen,purchase.outcome.max_potential_roi,
            purchase.outcome.days_to_first_win,
        )

    def load_database(self, db: Database) -> list[PerformanceSample]:
        db.migrate()
        with db.connect() as connection:
            rows=connection.execute("""SELECT virtual_purchase_id,status,snapshot_json,outcome_json,
                fee_source,fee_model_version FROM virtual_purchases""").fetchall()
        samples=[]
        for row in rows:
            snapshot=json.loads(row[2]); outcome=json.loads(row[3])
            samples.append(PerformanceSample(
                row[0],tuple(sorted(set(snapshot.get("signal_types") or ()))),
                row[4] or snapshot.get("fee_source") or "DEFAULT_ESTIMATE",
                row[5] or snapshot.get("fee_model_version") or "estimate_v1",
                VirtualPurchaseStatus(row[1]),int(snapshot.get("opportunity_score",0)),
                snapshot.get("confidence"),snapshot.get("history_quality"),snapshot.get("amazon_owned"),
                snapshot.get("sales_rank"),snapshot.get("new_offer_count"),
                outcome.get("max_potential_profit_yen"),outcome.get("max_potential_roi"),
                outcome.get("days_to_first_win"),
            ))
        return samples

    def analyze(
        self, samples: list[PerformanceSample], *, fee_source: str | None = None,
        fee_model_version: str | None = None,
    ) -> tuple[StrategyPerformanceReport, ...]:
        selected=[sample for sample in samples if (fee_source is None or sample.fee_source==fee_source) and (fee_model_version is None or sample.fee_model_version==fee_model_version)]
        fee_groups=_group(selected,lambda sample:(sample.fee_source,sample.fee_model_version))
        return tuple(self._report(source,version,group) for (source,version),group in sorted(fee_groups.items()))

    def analyze_database(self, db: Database, **filters) -> tuple[StrategyPerformanceReport, ...]:
        return self.analyze(self.load_database(db),**filters)

    def _report(self, source: str, version: str, samples: list[PerformanceSample]) -> StrategyPerformanceReport:
        strategies=[]
        for key,group in sorted(_group(samples,lambda sample:sample.strategy_key).items()):
            types=tuple(key.split("+")) if key else ()
            strategies.append(StrategyPerformance(key,types,source,version,_metrics(group)))
        return StrategyPerformanceReport(
            source,version,_metrics(samples),tuple(strategies),
            _buckets(samples,_score_bucket),_buckets(samples,_signal_count_bucket),
            _buckets(samples,_amazon_owned_bucket),_buckets(samples,_rank_bucket),
            _buckets(samples,_offer_bucket),_buckets(samples,_confidence_bucket),
            _buckets(samples,lambda sample:sample.history_quality or "unknown"),
        )


def _metrics(samples: list[PerformanceSample]) -> PerformanceMetrics:
    wins=[sample for sample in samples if sample.status==VirtualPurchaseStatus.WIN]
    losses=[sample for sample in samples if sample.status==VirtualPurchaseStatus.LOSS]
    closed=len(wins)+len(losses)
    profits=[sample.max_potential_profit_yen for sample in samples if sample.max_potential_profit_yen is not None]
    rois=[sample.max_potential_roi for sample in samples if sample.max_potential_roi is not None]
    days=[sample.days_to_first_win for sample in wins if sample.days_to_first_win is not None]
    return PerformanceMetrics(
        len(samples),closed,len(wins),len(losses),
        sum(sample.status==VirtualPurchaseStatus.OPEN for sample in samples),
        sum(sample.status==VirtualPurchaseStatus.EXPIRED for sample in samples),
        round(len(wins)/closed,4) if closed else None,
        _average(profits),_median(profits),_average(rois),_median(rois),
        _average(days),_median(days),
        SampleQuality.USABLE if closed>=30 else SampleQuality.EARLY if closed>=10 else SampleQuality.INSUFFICIENT,
        tuple(sample.virtual_purchase_id for sample in samples),
    )


def _average(values): return round(mean(values),4) if values else None
def _median(values): return round(median(values),4) if values else None


def _group(samples, key):
    groups=defaultdict(list)
    for sample in samples: groups[key(sample)].append(sample)
    return groups


def _buckets(samples, key):
    return tuple(BucketPerformance(name,_metrics(group)) for name,group in sorted(_group(samples,key).items()))


def _score_bucket(sample):
    score=sample.opportunity_score
    if score<60:return "0-59"
    if score<70:return "60-69"
    if score<80:return "70-79"
    if score<90:return "80-89"
    return "90-100"


def _signal_count_bucket(sample):
    count=len(set(sample.signal_types)); return "1" if count==1 else "2" if count==2 else "3+"


def _amazon_owned_bucket(sample):
    return "unknown" if sample.amazon_owned is None else "true" if sample.amazon_owned else "false"


def _rank_bucket(sample):
    value=sample.sales_rank
    if value is None:return "unknown"
    if value<=10000:return "1-10000"
    if value<=50000:return "10001-50000"
    if value<=150000:return "50001-150000"
    return "150001+"


def _offer_bucket(sample):
    value=sample.new_offer_count
    if value is None:return "unknown"
    if value<=3:return "0-3"
    if value<=7:return "4-7"
    if value<=15:return "8-15"
    return "16+"


def _confidence_bucket(sample):
    value=sample.confidence
    if value is None:return "unknown"
    if value<60:return "0-59"
    if value<70:return "60-69"
    if value<80:return "70-79"
    if value<90:return "80-89"
    return "90-100"
