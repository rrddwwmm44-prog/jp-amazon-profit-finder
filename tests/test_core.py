import unittest
from app.domain import ProductSignal,Verification
from app.services.product_matcher import normalize_jan,match_products
from app.services.profit_calculator import calculate
from app.services.scoring import score
from app.services.confidence import assess

class CoreTests(unittest.TestCase):
    def test_jan(self): self.assertEqual(normalize_jan("4901234567894"),"4901234567894"); self.assertIsNone(normalize_jan("4901234567890"))
    def test_match_jan(self):
        a=ProductSignal("a","r","x","m",jan="4901234567894"); b=ProductSignal("b","r","y","m",jan="4901234567894")
        self.assertEqual(match_products(a,b),(100,"JAN一致"))
    def test_profit(self):
        p=calculate(8980,3980); self.assertEqual(p.profit_yen,3652); self.assertGreater(p.roi,.9)
    def test_score_cap(self):
        s=ProductSignal("x","x","x","x",amazon_present=False,is_new=True,is_discontinued=True,seller_count=1,sales_rank=1,amazon_owned=False,price_drop_rate=.4,amazon_rise_rate=.4,strong_seller_entries=5)
        self.assertEqual(score(s,calculate(10000,1000)),100)
    def test_confidence(self):
        s=ProductSignal("x","x","x","x",jan="4901234567894",asin="B",evidence={"matching_sources":["amazon","rakuten"]})
        self.assertEqual(assess(s),(100,Verification.VERIFIED))
