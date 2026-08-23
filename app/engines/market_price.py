from app.engines.base import EngineContext, EngineResult, EngineStatus
from app.runner import run as run_legacy_pipeline


class MarketPriceEngine:
    """Compatibility wrapper around the existing market pipeline."""

    name = "market_price"

    def run(self, context: EngineContext) -> EngineResult:
        job_id, candidates = run_legacy_pipeline(context.db, context.settings, context.mode)
        count = len(candidates)
        return EngineResult(
            self.name,
            EngineStatus.SUCCESS,
            processed_count=count,
            candidate_count=count,
            job_id=job_id,
        )
