from app.domain import ProductSignal

def score(s: ProductSignal, profit=None) -> int:
    n=0
    if s.amazon_present is False: n+=25
    if s.is_new or s.is_renewal: n+=18
    if s.is_discontinued: n+=18
    if s.seller_count is not None: n += 18 if s.seller_count<=3 else 12 if s.seller_count<=5 else 0
    if s.amazon_owned is False: n+=10
    if s.sales_rank is not None: n += 15 if s.sales_rank<=10000 else 10 if s.sales_rank<=50000 else 5 if s.sales_rank<=100000 else 0
    if s.price_drop_rate>=.30: n+=15
    elif s.price_drop_rate>=.20: n+=10
    if profit:
        n += 15 if profit.roi>=.30 else 10 if profit.roi>=.20 else 0
        n += 10 if profit.profit_yen>=3000 else 5 if profit.profit_yen>=1000 else 0
    n += 18 if s.strong_seller_entries>=5 else 12 if s.strong_seller_entries>=3 else 0
    n += 10 if s.amazon_rise_rate>=.30 else 7 if s.amazon_rise_rate>=.20 else 0
    return min(100,n)
