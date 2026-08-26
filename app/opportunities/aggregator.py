from __future__ import annotations

from collections import defaultdict
from hashlib import sha256

from app.opportunities.models import Opportunity, OpportunityStatus, OpportunitySummary, Signal


class OpportunityAggregator:
    def aggregate(self, signals: list[Signal]) -> list[Opportunity]:
        groups: dict[str, list[Signal]] = defaultdict(list)
        for index, signal in enumerate(signal for signal in signals if signal.candidate):
            groups[self._identity(signal, index)].append(signal)
        return sorted((self._build(key, group) for key, group in groups.items()),
                      key=lambda item: item.opportunity_score, reverse=True)

    @staticmethod
    def _identity(signal: Signal, index: int) -> str:
        if signal.asin: return f"asin:{signal.asin.upper()}"
        if signal.jan: return f"jan:{signal.jan}"
        # Deliberately unique: names must never merge products.
        return f"unidentified:{index}:{signal.source_engine}:{signal.observed_at}"

    def _build(self, identity: str, signals: list[Signal]) -> Opportunity:
        types={signal.signal_type for signal in signals}
        score=min(100,max(signal.score for signal in signals)+(10 if len(types)>=2 else 0)+(5 if {"amazon_arbitrage","seller_decline"}<=types else 0))
        ordered=tuple(sorted(signals,key=lambda signal: signal.score,reverse=True))
        reasons=_unique(signal.reason for signal in ordered if signal.reason)
        risks=_risks(ordered)
        confidences=[signal.confidence for signal in ordered if signal.confidence is not None]
        urgencies=[signal.urgency_hint for signal in ordered if signal.urgency_hint is not None]
        identity_type,identity_value=identity.split(":",1)
        return Opportunity(
            opportunity_id=sha256(identity.encode()).hexdigest()[:24],
            identity_type=identity_type, identity_value=identity_value,
            asin=next((s.asin for s in ordered if s.asin),None),
            jan=next((s.jan for s in ordered if s.jan),None),
            product_name=next((s.product_name for s in ordered if s.product_name),None),
            manufacturer=next((s.manufacturer for s in ordered if s.manufacturer),None),
            observed_at=max(s.observed_at for s in ordered), opportunity_score=score,
            urgency_score=max(urgencies) if urgencies else None,
            confidence=max(confidences) if confidences else None,
            status=OpportunityStatus.OPEN, signal_count=len(ordered), signals=ordered,
            reasons=reasons, risks=risks, evidence=tuple(s.evidence for s in ordered),
            summary=_summary(ordered,reasons),
        )


def _summary(signals: tuple[Signal, ...], reasons: tuple[str, ...]) -> OpportunitySummary:
    arbitrage=next((s for s in signals if s.signal_type=="amazon_arbitrage"),None)
    amazon_e=arbitrage.evidence if arbitrage else {}
    return OpportunitySummary(
        purchase_price=amazon_e.get("purchase_price"), expected_sale_price=amazon_e.get("expected_sale_price"),
        expected_profit_yen=amazon_e.get("profit_yen"), roi=amazon_e.get("roi"),
        sales_rank=_signal_value(signals,"sales_rank"), new_offer_count=_signal_value(signals,"new_offer_count","new_offer_count_current"),
        amazon_owned=_signal_value(signals,"amazon_owned"), current_amazon_price=amazon_e.get("purchase_price"),
        marketplace_new_price=_signal_value(signals,"marketplace_new_price"), median_price_30d=amazon_e.get("median_price_30d"),
        median_price_90d=amazon_e.get("median_price_90d"), history_quality=next((s.quality for s in signals if s.quality),None),
        signal_types=tuple(s.signal_type for s in signals), primary_reason=reasons[0] if reasons else None,
    )


def _risks(signals: tuple[Signal, ...]) -> tuple[str, ...]:
    risks=[]
    for signal in signals:
        if signal.evidence.get("amazon_owned_return_risk"): risks.append("amazon_owned_return_risk")
        if signal.quality in {"partial","insufficient"}: risks.append(f"{signal.quality}_history")
        risks.extend(signal.evidence.get("risks",()))
    return _unique(risks)


def _unique(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _signal_value(signals: tuple[Signal, ...], *keys: str):
    return next((signal.evidence[key] for signal in signals for key in keys if signal.evidence.get(key) is not None),None)
