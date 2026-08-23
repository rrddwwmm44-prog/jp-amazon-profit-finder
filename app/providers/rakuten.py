from __future__ import annotations
import os, re
from app.domain import ProductSignal
from app.services.product_matcher import normalize_jan
from app.utils.http import JsonHttpClient
from .base import Provider
from .live import ProviderUnavailable

class RakutenProvider(Provider):
    name="rakuten"
    endpoint="https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701"
    def __init__(self, application_id=None, access_key=None, queries=None, client=None):
        self.application_id=application_id or os.getenv("RAKUTEN_APP_ID")
        self.access_key=access_key or os.getenv("RAKUTEN_ACCESS_KEY")
        self.queries=queries if queries is not None else [x.strip() for x in os.getenv("MARKET_SEARCH_QUERIES","").split(",") if x.strip()]
        self.client=client or JsonHttpClient()
    def fetch(self,cursor=None):
        if not self.application_id or not self.access_key: raise ProviderUnavailable("rakuten: RAKUTEN_APP_ID / RAKUTEN_ACCESS_KEY が未設定")
        if not self.queries: raise ProviderUnavailable("rakuten: MARKET_SEARCH_QUERIES が未設定")
        start=int(cursor or 0); signals=[]
        for query in self.queries[start:]:
            data=self.client.get(self.endpoint,{"applicationId":self.application_id,"keyword":query,"hits":30,"page":1,"format":"json","formatVersion":2},headers={"accessKey":self.access_key})
            for item in data.get("items",[]): signals.append(self._signal(item))
            start+=1
        return signals,str(start)
    @staticmethod
    def _signal(item):
        text=" ".join(str(item.get(k,"")) for k in ("itemName","itemCaption"))
        jan=next((normalize_jan(x) for x in re.findall(r"(?<!\d)\d{13}(?!\d)",text) if normalize_jan(x)),None)
        return ProductSignal("rakuten","楽天市場で販売を確認",item.get("itemName","") or "名称不明",item.get("shopName","") or "unknown",jan=jan,source_price=float(item["itemPrice"]) if item.get("itemPrice") is not None else None,source_url=item.get("itemUrl","") or "",evidence={"shop":item.get("shopName"),"item_code":item.get("itemCode"),"availability":item.get("availability"),"postage_flag":item.get("postageFlag"),"matching_sources":["rakuten"]})
