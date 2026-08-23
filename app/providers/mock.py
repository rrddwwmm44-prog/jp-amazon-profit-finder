from .base import Provider
from app.domain import ProductSignal
class MockProvider(Provider):
    name="mock"
    def fetch(self,cursor=None):
        rows=[
          ProductSignal("manufacturer","旧商品→新JANリニューアル / Amazon未登録","高機能ボトル 500ml","サンプル工業",jan="4901234567894",model="BT-500N",amazon_present=False,is_renewal=True,source_price=2480,source_url="https://example.invalid/products/bt500",evidence={"matching_sources":["manufacturer","rakuten","amazon"]}),
          ProductSignal("market","メーカー廃番 / 出品者2→1 / Amazon価格+38%","交換フィルター 3個入","例示電機",jan="4901234560017",asin="B0MOCK0001",amazon_price=8980,source_price=3980,seller_count=1,sales_rank=9200,amazon_owned=False,is_discontinued=True,amazon_rise_rate=.38,strong_seller_entries=3,evidence={"matching_sources":["manufacturer","rakuten","amazon"]}),
          ProductSignal("rakuten","7日平均比-28%","定番クリーナー","テスト製作所",jan="4901234560024",asin="B0MOCK0002",amazon_price=4200,source_price=2500,seller_count=8,sales_rank=76000,price_drop_rate=.28,evidence={"matching_sources":["rakuten"]})]
        return rows,"done"
