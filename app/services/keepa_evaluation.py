from __future__ import annotations

from app.config import Settings
from app.providers.keepa import KeepaProvider
from app.services.amazon_arbitrage import evaluate_arbitrage
from app.services.keepa_adapters import to_arbitrage_input, to_seller_decline_input
from app.services.keepa_budget import build_keepa_budget
from app.services.seller_decline import evaluate_seller_decline
from app.storage.db import Database


def evaluate_keepa_asin(provider: KeepaProvider, asin: str, settings: Settings, db: Database) -> dict:
    """Fetch and evaluate exactly one validated ASIN with the existing engine rules."""
    result = provider.get_product(asin)
    history = result.history
    if history is None:
        raise ValueError("Keepa history is not available")

    arbitrage = evaluate_arbitrage(to_arbitrage_input(history), settings)
    decline = evaluate_seller_decline(to_seller_decline_input(history), settings)
    budget = build_keepa_budget(db)
    tokens = result.tokens

    return {
        "keepa": {
            "asin": result.product.asin,
            "marketplace": result.product.marketplace,
            "title": result.product.title,
            "cache_hit": result.cache_hit,
            "observed_at": history.observed_at,
        },
        "current": {
            "amazon_price": history.amazon_price_current,
            "marketplace_new_price": history.current_price,
            "sales_rank": history.current_sales_rank,
            "new_offer_count": history.current_new_offer_count,
            "amazon_owned": history.amazon_owned_current,
        },
        "history": {
            "price_7d": history.price_7d,
            "price_30d": history.price_30d,
            "price_90d": history.price_90d,
            "median_price_30d": history.median_price_30d,
            "median_price_90d": history.median_price_90d,
            "sales_rank_7d": history.sales_rank_7d,
            "sales_rank_30d": history.sales_rank_30d,
            "sales_rank_90d": history.sales_rank_90d,
            "new_offer_count_7d": history.new_offer_count_7d,
            "new_offer_count_30d": history.new_offer_count_30d,
            "new_offer_count_90d": history.new_offer_count_90d,
            "quality": history.quality.value,
        },
        "amazon_arbitrage": {
            "candidate": arbitrage.is_candidate,
            "score": arbitrage.arbitrage_score,
            "purchase_price": arbitrage.item.purchase_price,
            "expected_sale_price": arbitrage.expected_sale_price,
            "expected_profit": arbitrage.profit.profit_yen if arbitrage.profit else None,
            "roi": arbitrage.profit.roi if arbitrage.profit else None,
            "drop_rate": arbitrage.drop_rate,
            "reject_reasons": list(arbitrage.reject_reasons),
        },
        "seller_decline": {
            "candidate": decline.is_candidate,
            "new_offer_decline_score": decline.seller_decline_score,
            "decline_rates": decline.decline_rates,
            "acceleration": decline.decline_acceleration,
            "demand_trend": decline.demand_trend.value,
            "price_trends": decline.price_trends,
            "reject_reasons": list(decline.reject_reasons),
        },
        "tokens": {
            "tokens_consumed": 0 if result.cache_hit else tokens.tokens_consumed if tokens else None,
            "tokens_left": tokens.tokens_left if tokens else budget["tokens_left"],
            "refill_rate": tokens.refill_rate if tokens else budget["refill_rate_tokens_per_min"],
            "budget_status": budget["status"],
        },
    }
