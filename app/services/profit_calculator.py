from dataclasses import dataclass

@dataclass(frozen=True)
class ProfitResult: profit_yen: float; margin: float; roi: float
def calculate(sale_price: float, purchase_price: float, shipping: float=0, referral_rate: float=.10, fulfillment_fee: float=450, other_cost: float=0) -> ProfitResult:
    costs=purchase_price+shipping+sale_price*referral_rate+fulfillment_fee+other_cost
    profit=sale_price-costs
    return ProfitResult(round(profit,2), round(profit/sale_price,4) if sale_price else 0, round(profit/(purchase_price+shipping),4) if purchase_price+shipping else 0)
