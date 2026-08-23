import re
import unicodedata

def normalize_jan(value: str|None) -> str|None:
    if not value: return None
    digits = re.sub(r"\D", "", unicodedata.normalize("NFKC", value))
    if len(digits) not in (8, 13): return None
    nums = [int(x) for x in digits]
    body, check = nums[:-1], nums[-1]
    total = sum(n * (3 if (len(body)-i) % 2 else 1) for i, n in enumerate(body))
    return digits if (10-total%10)%10 == check else None

def match_products(a, b):
    if normalize_jan(a.jan) and normalize_jan(a.jan)==normalize_jan(b.jan): return 100, "JAN一致"
    if a.model and b.model and a.model.casefold()==b.model.casefold(): return 90, "型番一致"
    na=set(unicodedata.normalize("NFKC", a.product_name).casefold().split()); nb=set(unicodedata.normalize("NFKC", b.product_name).casefold().split())
    score=int(70*len(na&nb)/max(1,len(na|nb)))
    return score, "商品名類似（補助判定）"
