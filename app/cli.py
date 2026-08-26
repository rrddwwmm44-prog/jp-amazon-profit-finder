from __future__ import annotations
import argparse, json, os, platform, sqlite3, sys
from pathlib import Path
from app.config import Settings
from app.storage.db import Database
from app.runner import run
from app.engines import AmazonArbitrageEngine, EngineContext, EngineRegistry, EngineStatus, MarketPriceEngine, SellerDeclineEngine, UnknownEngineError
from app.services.keepa_budget import build_keepa_budget
from app.providers.keepa import KeepaError, KeepaHttpError, KeepaProvider, KeepaTokensExhausted
from app.services.keepa_evaluation import evaluate_keepa_asin
from app.opportunities.aggregator import OpportunityAggregator
from app.opportunities.fixtures import mock_signals

def engine_registry():
    registry=EngineRegistry(); registry.register(MarketPriceEngine()); registry.register(AmazonArbitrageEngine()); registry.register(SellerDeclineEngine()); return registry

def doctor(settings,db):
    checks=[]
    checks.append(("Python",sys.version_info >= (3,11),platform.python_version()))
    try: db.migrate(); checks.append(("SQLite/DB migration",True,f"SQLite {sqlite3.sqlite_version} / schema v{db.schema_version()}"))
    except Exception as e: checks.append(("SQLite/DB migration",False,str(e)))
    checks.append(("書き込み権限",os.access(settings.db_path.parent,os.W_OK),str(settings.db_path.parent)))
    checks.append((".env",Path(".env").exists(),"未作成でも mock は実行可能"))
    groups={"Amazon":["AMAZON_SP_API_REFRESH_TOKEN","AMAZON_SP_API_CLIENT_ID","AMAZON_SP_API_CLIENT_SECRET"],"楽天":["RAKUTEN_APP_ID","RAKUTEN_ACCESS_KEY"],"Yahoo":["YAHOO_CLIENT_ID"],"Keepa":["KEEPA_API_KEY"],"検索語":["MARKET_SEARCH_QUERIES"],"Google Sheets":["GOOGLE_SHEETS_ID","GOOGLE_SERVICE_ACCOUNT_JSON"]}
    for name,keys in groups.items(): checks.append((name,all(os.getenv(k) for k in keys),"設定済み" if all(os.getenv(k) for k in keys) else "未設定（mock/CSV利用可）"))
    for name,ok,detail in checks: print(f"[{'OK' if ok else 'WARN'}] {name}: {detail}")
    print("[INFO] Engines: "+", ".join(f"{name}={'enabled' if enabled else 'disabled'}" for name,enabled in settings.engine_flags.items()))
    return 0 if checks[0][1] and checks[1][1] and checks[2][1] else 1
def status(db):
    db.migrate()
    with db.connect() as c:
        rows=c.execute("SELECT id,mode,status,cursor,started_at,completed_at,error FROM jobs ORDER BY id DESC LIMIT 10").fetchall()
    print(json.dumps([dict(r) for r in rows],ensure_ascii=False,indent=2)); return 0
def keepa_budget(db):
    db.migrate(); print(json.dumps(build_keepa_budget(db),ensure_ascii=False,indent=2)); return 0
def main(argv=None):
    p=argparse.ArgumentParser(description="日本Amazon 利益商品発見システム"); sub=p.add_subparsers(dest="cmd",required=True)
    sub.add_parser("doctor"); r=sub.add_parser("run-all"); r.add_argument("--mode",choices=["mock","live"],default="mock"); one=sub.add_parser("run"); one.add_argument("engine"); one.add_argument("--mode",choices=["mock","live"],default="mock"); sub.add_parser("resume"); sub.add_parser("status"); sub.add_parser("keepa-budget"); keepa_eval=sub.add_parser("keepa-evaluate"); keepa_eval.add_argument("asin"); opportunities=sub.add_parser("opportunities"); opportunities.add_argument("--mode",choices=["mock"],default="mock")
    a=p.parse_args(argv); settings=Settings.load(); db=Database(settings.db_path)
    if a.cmd=="doctor": return doctor(settings,db)
    if a.cmd=="status": return status(db)
    if a.cmd=="keepa-budget": return keepa_budget(db)
    if a.cmd=="opportunities":
        db.migrate(); items=OpportunityAggregator().aggregate(mock_signals(settings))
        for item in items: db.save_opportunity(item)
        report={"opportunity_count":len(items),"signal_count":sum(x.signal_count for x in items),"multi_signal_count":sum(x.signal_count>1 for x in items),"top_opportunities":[{"asin":x.asin,"name":x.product_name,"opportunity_score":x.opportunity_score,"urgency_score":x.urgency_score,"purchase_price":x.summary.purchase_price,"expected_profit":x.summary.expected_profit_yen,"roi":x.summary.roi,"signal_types":x.summary.signal_types,"reasons":x.reasons} for x in items[:10]]}
        print(json.dumps(report,ensure_ascii=False,indent=2)); return 0
    if a.cmd=="keepa-evaluate":
        api_key=os.getenv("KEEPA_API_KEY")
        if not api_key:
            print("Keepa API key is not configured",file=sys.stderr); return 2
        db.migrate()
        provider=KeepaProvider(
            api_key,db=db,cache_ttl_seconds=settings.keepa_cache_ttl_seconds,
            history_max_gap_days=settings.keepa_history_max_gap_days,
            history_minimum_median_samples=settings.keepa_history_minimum_median_samples,
        )
        try:
            report=evaluate_keepa_asin(provider,a.asin,settings,db)
        except ValueError as exc:
            print(str(exc),file=sys.stderr); return 2
        except KeepaTokensExhausted:
            print("Keepa tokens are exhausted",file=sys.stderr); return 3
        except KeepaHttpError as exc:
            category="authentication error" if exc.status in {401,403} else "product not found" if exc.status==404 else "transport error"
            print(f"Keepa {category}",file=sys.stderr); return 3
        except KeepaError as exc:
            message=str(exc)
            category="product not found" if "no product" in message.lower() else "response incompatibility"
            print(f"Keepa {category}",file=sys.stderr); return 3
        print(json.dumps(report,ensure_ascii=False,indent=2)); return 0
    db.migrate()
    if a.cmd in {"run-all","run"}:
        registry=engine_registry(); context=EngineContext(settings,db,a.mode)
        try: results=registry.run_enabled(context) if a.cmd=="run-all" else [registry.run_one(a.engine.replace("-","_"),context)]
        except UnknownEngineError as exc: print(str(exc),file=sys.stderr); return 2
        for result in results:
            print(f"engine={result.engine_name} status={result.status.value} job_id={result.job_id} processed={result.processed_count} candidates={result.candidate_count}"+(f" error={result.error}" if result.error else ""))
        return 1 if any(result.status==EngineStatus.FAILED for result in results) else 0
    job,items=run(db,settings,"mock"); print(f"job_id={job} candidates={len(items)} score85+={sum(x.score>=settings.today_score_threshold for x in items)}"); return 0
