from app.domain import ProductSignal, Verification

def assess(signal: ProductSignal):
    sources=set(signal.evidence.get("matching_sources", [signal.source]))
    confidence=min(100, 35+15*len(sources)+(25 if signal.jan else 0)+(10 if signal.asin else 0)-(20 if signal.evidence.get("missing" ) else 0))
    verified=bool(signal.jan and len(sources)>=2 and (signal.asin or "amazon" in {s.lower() for s in sources}))
    return confidence, Verification.VERIFIED if verified else Verification.PROVISIONAL
