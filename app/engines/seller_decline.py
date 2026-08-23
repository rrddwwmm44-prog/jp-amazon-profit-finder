from __future__ import annotations

from typing import Protocol

from app.domain import MissingState
from app.engines.base import EngineContext, EngineResult, EngineStatus
from app.services.seller_decline import SellerDeclineAssessment, SellerDeclineInput, SellerObservation, evaluate_seller_decline


def observed(value: int) -> SellerObservation:
    return SellerObservation(value,MissingState.VERIFIED_ZERO if value == 0 else None)


class SellerDeclineSource(Protocol):
    def load(self) -> list[SellerDeclineInput]: ...


class MockSellerDeclineSource:
    def load(self) -> list[SellerDeclineInput]:
        missing=SellerObservation(None,MissingState.NOT_OBSERVED)
        return [
            SellerDeclineInput("B0SEL00001","ideal",observed(4),observed(8),observed(13),observed(18),5480,4800,4200,3980,26000,25000,False),
            SellerDeclineInput("B0SEL00002","demand-collapse",observed(3),observed(6),observed(10),observed(15),3000,3400,3800,4000,80000,20000,False),
            SellerDeclineInput("B0SEL00003","flat",observed(10),observed(9),observed(10),observed(10),5000,5000,5000,5000,30000,30000,False),
            SellerDeclineInput("B0SEL00004","temporary",observed(5),observed(10),observed(10),observed(10),5000,5000,5000,5000,30000,30000,False),
            SellerDeclineInput("B0SEL00005","moderate",observed(4),observed(7),observed(10),observed(14),5000,5000,5000,5000,31000,30000,False),
            SellerDeclineInput("B0SEL00006","missing-history",observed(5),observed(7),missing,missing,5000,5000,None,None,30000,None,False),
            SellerDeclineInput("B0SEL00007","amazon-owned",observed(4),observed(8),observed(13),observed(18),5480,4800,4200,3980,26000,25000,True),
            SellerDeclineInput("B0SEL00008","verified-zero",observed(0),observed(4),observed(8),observed(12),6000,5500,5000,4500,25000,26000,False),
            SellerDeclineInput("B0SEL00009","missing-current",missing,observed(4),observed(8),observed(12),None,5500,5000,4500,None,26000,False),
        ]


class SellerDeclineEngine:
    name="seller_decline"

    def __init__(self,source: SellerDeclineSource | None=None):
        self.source=source or MockSellerDeclineSource()
        self.last_assessments: list[SellerDeclineAssessment]=[]

    def run(self,context: EngineContext) -> EngineResult:
        if context.mode != "mock": return EngineResult(self.name,EngineStatus.SKIPPED,error="fixture_only")
        items=self.source.load()
        self.last_assessments=[evaluate_seller_decline(item,context.settings) for item in items]
        return EngineResult(self.name,EngineStatus.SUCCESS,processed_count=len(items),candidate_count=sum(item.is_candidate for item in self.last_assessments))
