from __future__ import annotations

from typing import Protocol

from app.engines.base import EngineContext, EngineResult, EngineStatus
from app.services.amazon_arbitrage import ArbitrageAssessment, ArbitrageInput, evaluate_arbitrage


class ArbitrageSource(Protocol):
    def load(self) -> list[ArbitrageInput]: ...


class MockArbitrageSource:
    def load(self) -> list[ArbitrageInput]:
        return [
            ArbitrageInput("B0ARB00001","strong",3000,5500,5600,sales_rank=12000,new_seller_count=4,amazon_owned=False,demand_quality="good"),
            ArbitrageInput("B0ARB00002","low-profit",3000,4300,4400,sales_rank=20000,new_seller_count=3),
            ArbitrageInput("B0ARB00003","low-roi",10000,13000,13500,sales_rank=25000,new_seller_count=4),
            ArbitrageInput("B0ARB00004","small-drop",4500,5000,5100,sales_rank=20000,new_seller_count=4),
            ArbitrageInput("B0ARB00005","missing-history",3000,sales_rank=20000,new_seller_count=4),
            ArbitrageInput("B0ARB00006","missing-rank",3000,5500,5600,new_seller_count=4,amazon_owned=False),
            ArbitrageInput("B0ARB00007","amazon-owned",3000,5500,5600,sales_rank=12000,new_seller_count=4,amazon_owned=True,demand_quality="good"),
        ]


class AmazonArbitrageEngine:
    name="amazon_arbitrage"

    def __init__(self,source: ArbitrageSource | None=None):
        self.source=source or MockArbitrageSource()
        self.last_assessments: list[ArbitrageAssessment]=[]

    def run(self,context: EngineContext) -> EngineResult:
        if context.mode != "mock":
            return EngineResult(self.name,EngineStatus.SKIPPED,error="fixture_only")
        items=self.source.load()
        self.last_assessments=[evaluate_arbitrage(item,context.settings) for item in items]
        return EngineResult(self.name,EngineStatus.SUCCESS,processed_count=len(items),candidate_count=sum(item.is_candidate for item in self.last_assessments))
