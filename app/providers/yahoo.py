from __future__ import annotations
import os
from app.domain import ProductSignal
from app.services.product_matcher import normalize_jan
from app.utils.http import JsonHttpClient
from .base import Provider
from .live import ProviderUnavailable

class YahooProvider(Provider):
    name="yahoo"
    endpoint="https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"
    def __init__(self, client_id=None, queries=None, client=None):
        self.client_id=client_id or os.getenv("YAHOO_CLIENT_ID")
        self.queries=queries if queries is not None else [x.strip() for x in os.getenv("MARKET_SEARCH_QUERIES","").split(",") if x.strip()]
        self.client=client or JsonHttpClient(min_interval=1.05)
    def fetch(self,cursor=None):
        if not self.client_id: raise ProviderUnavailable("yahoo: YAHOO_CLIENT_ID が未設定")
        if not self.queries: raise ProviderUnavailable("yahoo: MARKET_SEARCH_QUERIES が未設定")
        start=int(cursor or 0); signals=[]
        for query in self.queries[start:]:
            data=self.client.get(self.endpoint,{"appid":self.client_id,"query":query,"results":50,"start":1,"in_stock":"true"})
            for item in data.get("hits",[]): signals.append(self._signal(item))
            start+=1
        return signals,str(start)
    @staticmethod
    def _signal(item):
        seller=item.get("seller") or {}; shipping=item.get("shipping") or {}
        return ProductSignal("yahoo","Yahoo!ショッピングで販売を確認",item.get("name","") or "名称不明",seller.get("name","") or "unknown",jan=normalize_jan(item.get("janCode")),source_price=float(item["price"]) if item.get("price") is not None else None,source_url=item.get("url","") or "",evidence={"seller_id":seller.get("sellerId"),"in_stock":item.get("inStock"),"shipping":shipping,"item_code":item.get("code"),"matching_sources":["yahoo"]})
