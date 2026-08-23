from __future__ import annotations
import csv, logging
from pathlib import Path
from app.domain import Candidate
from app.providers.mock import MockProvider
from app.providers.live import UnavailableProvider,ProviderUnavailable
from app.providers.rakuten import RakutenProvider
from app.providers.yahoo import YahooProvider
from app.services.profit_calculator import calculate
from app.services.scoring import score
from app.services.confidence import assess

LOG=logging.getLogger(__name__)
HEADERS=["発見日時","Score","Confidence","判定","発見理由","商品名","メーカー","JAN","ASIN","Amazon URL","Amazon価格","仕入価格","仕入先","仕入先URL","利益額","利益率","ROI","Sales Rank","出品者数","Amazon本体","新商品","リニューアル","廃番","価格急落率","Amazon価格上昇率","強いセラー参入数","最終確認日時","ステータス","メモ"]
def providers(mode):
    if mode=="mock": return [MockProvider()]
    return [RakutenProvider(),YahooProvider(),UnavailableProvider("amazon",["AMAZON_SP_API_REFRESH_TOKEN","AMAZON_SP_API_CLIENT_ID","AMAZON_SP_API_CLIENT_SECRET"])]
def export(candidates,path:Path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.writer(f); w.writerow(HEADERS)
        for c in sorted(candidates,key=lambda x:x.score,reverse=True):
            s=c.signal; w.writerow([s.observed_at,c.score,c.confidence,c.verification.value,s.reason,s.product_name,s.manufacturer,s.jan,s.asin,s.amazon_url,s.amazon_price,s.source_price,s.source,s.source_url,c.profit_yen,c.margin,c.roi,s.sales_rank,s.seller_count,s.amazon_owned,s.is_new,s.is_renewal,s.is_discontinued,s.price_drop_rate,s.amazon_rise_rate,s.strong_seller_entries,s.observed_at,c.status,""])
def run(db,settings,mode="mock"):
    job=db.start_job(mode); out=[]
    try:
        for p in providers(mode):
            try: signals,cursor=p.fetch(None)
            except ProviderUnavailable as e: db.record_error(job,p.name,e); LOG.warning("provider=%s status=unavailable",p.name); continue
            except Exception as e:
                # A provider outage or malformed response must not stop other providers.
                # KeyboardInterrupt/SystemExit still propagate because they do not inherit
                # from Exception.
                db.record_error(job,p.name,e)
                LOG.exception("provider=%s status=failed",p.name)
                continue
            for s in signals:
                profit=calculate(s.amazon_price,s.source_price,s.shipping) if s.amazon_price is not None and s.source_price is not None else None
                conf,verification=assess(s); c=Candidate(s,score(s,profit),conf,verification,profit.profit_yen if profit else None,profit.margin if profit else None,profit.roi if profit else None)
                db.save_candidate(job,c); out.append(c)
        export(out,Path("data/exports/candidates.csv")); db.finish(job)
        return job,out
    except BaseException as e:
        db.finish(job,"INTERRUPTED",error=str(e)); raise
