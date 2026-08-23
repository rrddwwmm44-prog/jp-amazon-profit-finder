import unittest
from app.providers.rakuten import RakutenProvider
from app.providers.yahoo import YahooProvider

class FakeClient:
    def __init__(self,payload): self.payload=payload; self.calls=[]
    def get(self,url,params,headers=None): self.calls.append((url,params,headers)); return self.payload

class MarketProviderTests(unittest.TestCase):
    def test_rakuten_maps_v2_response_and_auth_header(self):
        client=FakeClient({"items":[{"itemName":"商品 4901234567894","itemPrice":2480,"itemUrl":"https://example/r","shopName":"店","itemCode":"s:1","availability":1}]})
        rows,cursor=RakutenProvider("app","key",["商品"],client).fetch()
        self.assertEqual((rows[0].jan,rows[0].source_price,cursor),("4901234567894",2480.0,"1")); self.assertEqual(client.calls[0][2]["accessKey"],"key")
    def test_yahoo_maps_v3_response(self):
        client=FakeClient({"hits":[{"name":"商品","price":1980,"url":"https://example/y","janCode":"4901234567894","inStock":True,"seller":{"sellerId":"s","name":"店"}}]})
        rows,cursor=YahooProvider("client",["商品"],client).fetch()
        self.assertEqual((rows[0].jan,rows[0].manufacturer,rows[0].source_price,cursor),("4901234567894","店",1980.0,"1")); self.assertEqual(client.calls[0][1]["appid"],"client")
