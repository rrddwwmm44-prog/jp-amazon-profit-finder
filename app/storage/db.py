from __future__ import annotations
import json, sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from app.storage.migrations import BASELINE_STATEMENTS, apply_migrations, current_version

# Kept as a compatibility aid for code that imports the old schema constant.
SCHEMA = ";\n".join(BASELINE_STATEMENTS) + ";"
class ClosingConnection(sqlite3.Connection):
    """トランザクションを確定/取消した後、Windowsでも確実にファイルを解放する。"""
    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()

class Database:
    def __init__(self,path:Path): self.path=path
    def connect(self):
        self.path.parent.mkdir(parents=True,exist_ok=True); c=sqlite3.connect(self.path,timeout=30,factory=ClosingConnection); c.row_factory=sqlite3.Row; c.execute("PRAGMA journal_mode=WAL"); return c
    def migrate(self):
        with self.connect() as c: return apply_migrations(c)
    def schema_version(self):
        with self.connect() as c: return current_version(c)
    def start_job(self,mode):
        with self.connect() as c: return c.execute("INSERT INTO jobs(mode,status) VALUES(?,?)",(mode,"RUNNING")).lastrowid
    def save_candidate(self,job_id,c):
        s=c.signal; key=s.jan or s.asin or f"{s.manufacturer}:{s.model or s.product_name}"
        vals=(job_id,key,s.observed_at,c.score,c.confidence,c.verification.value,s.reason,s.product_name,s.manufacturer,s.jan,s.asin,s.amazon_price,s.source_price,c.profit_yen,c.margin,c.roi,s.seller_count,s.sales_rank,c.status,json.dumps(s.evidence,ensure_ascii=False))
        with self.connect() as db: db.execute("INSERT OR REPLACE INTO candidates(job_id,identity_key,observed_at,score,confidence,verification,reason,product_name,manufacturer,jan,asin,amazon_price,source_price,profit_yen,margin,roi,seller_count,sales_rank,status,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",vals)
    def finish(self,job_id,status="COMPLETED",cursor="done",error=None):
        with self.connect() as c: c.execute("UPDATE jobs SET status=?,cursor=?,completed_at=CURRENT_TIMESTAMP,error=? WHERE id=?",(status,cursor,error,job_id))
    def record_error(self,job_id,provider,exc):
        with self.connect() as c: c.execute("INSERT INTO errors(job_id,provider,error_class,message) VALUES(?,?,?,?)",(job_id,provider,type(exc).__name__,str(exc)))
    def get_keepa_cache(self,asin,marketplace,ttl_seconds):
        cutoff=(datetime.now(timezone.utc)-timedelta(seconds=ttl_seconds)).isoformat()
        with self.connect() as c:
            row=c.execute("SELECT result_json,observed_at FROM keepa_cache WHERE asin=? AND marketplace=? AND observed_at>=?",(asin,marketplace,cutoff)).fetchone()
        return (json.loads(row[0]),row[1]) if row else None
    def save_keepa_cache(self,asin,marketplace,observed_at,result):
        payload=json.dumps(result,ensure_ascii=False,separators=(",",":"))
        with self.connect() as c:
            c.execute("INSERT INTO keepa_cache(asin,marketplace,observed_at,result_json) VALUES(?,?,?,?) ON CONFLICT(asin,marketplace) DO UPDATE SET observed_at=excluded.observed_at,result_json=excluded.result_json",(asin,marketplace,observed_at,payload))
    def record_keepa_usage(self,observed_at,operation,asin,tokens,status,source="jp-amazon-profit-finder"):
        values=(observed_at,operation,asin,tokens.tokens_consumed if tokens else None,tokens.tokens_left if tokens else None,tokens.refill_rate if tokens else None,tokens.refill_in if tokens else None,tokens.token_flow_reduction if tokens else None,tokens.processing_time_ms if tokens else None,status,source)
        with self.connect() as c:
            c.execute("INSERT INTO keepa_usage(observed_at,operation,asin,tokens_consumed,tokens_left,refill_rate,refill_in,token_flow_reduction,processing_time_ms,status,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)",values)
    def record_keepa_cache_hit(self,observed_at,operation,asin,source="jp-amazon-profit-finder"):
        with self.connect() as c:
            c.execute("INSERT INTO keepa_cache_hits(observed_at,operation,asin,source) VALUES(?,?,?,?)",(observed_at,operation,asin,source))
    def keepa_usage_since(self,observed_at):
        with self.connect() as c:
            return [dict(row) for row in c.execute("SELECT * FROM keepa_usage WHERE observed_at>=? ORDER BY observed_at",(observed_at,))]
    def keepa_cache_hits_since(self,observed_at):
        with self.connect() as c:
            return [dict(row) for row in c.execute("SELECT * FROM keepa_cache_hits WHERE observed_at>=? ORDER BY observed_at",(observed_at,))]
    def latest_keepa_usage(self,limit=2):
        with self.connect() as c:
            return [dict(row) for row in c.execute("SELECT * FROM keepa_usage ORDER BY observed_at DESC,id DESC LIMIT ?",(limit,))]
    def save_opportunity(self,opportunity):
        summary=json.dumps(asdict(opportunity.summary),ensure_ascii=False,separators=(",",":"))
        with self.connect() as c:
            c.execute("""INSERT INTO opportunities(
                opportunity_id,identity_type,identity_value,asin,jan,product_name,manufacturer,
                observed_at,opportunity_score,urgency_score,confidence,status,signal_count,
                summary_json,reasons_json,risks_json,evidence_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(opportunity_id) DO UPDATE SET
                observed_at=excluded.observed_at,opportunity_score=excluded.opportunity_score,
                urgency_score=excluded.urgency_score,confidence=excluded.confidence,
                status=excluded.status,signal_count=excluded.signal_count,
                summary_json=excluded.summary_json,reasons_json=excluded.reasons_json,
                risks_json=excluded.risks_json,evidence_json=excluded.evidence_json""",(
                opportunity.opportunity_id,opportunity.identity_type,opportunity.identity_value,
                opportunity.asin,opportunity.jan,opportunity.product_name,opportunity.manufacturer,
                opportunity.observed_at,opportunity.opportunity_score,opportunity.urgency_score,
                opportunity.confidence,opportunity.status.value,opportunity.signal_count,summary,
                json.dumps(opportunity.reasons,ensure_ascii=False),json.dumps(opportunity.risks,ensure_ascii=False),
                json.dumps(opportunity.evidence,ensure_ascii=False,separators=(",",":")),
            ))
            for signal in opportunity.signals:
                c.execute("""INSERT OR IGNORE INTO opportunity_signals(
                    opportunity_id,signal_type,source_engine,asin,jan,score,candidate,
                    observed_at,reason,confidence,quality,urgency_hint,evidence_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
                    opportunity.opportunity_id,signal.signal_type,signal.source_engine,signal.asin,
                    signal.jan,signal.score,int(signal.candidate),signal.observed_at,signal.reason,
                    signal.confidence,signal.quality,signal.urgency_hint,
                    json.dumps(signal.evidence,ensure_ascii=False,separators=(",",":")),
                ))
    def save_virtual_purchase(self,purchase):
        snapshot=json.dumps(asdict(purchase.entry_snapshot),ensure_ascii=False,separators=(",",":"))
        outcome=json.dumps(asdict(purchase.outcome),ensure_ascii=False,separators=(",",":"))
        summary=json.dumps(asdict(purchase.summary),ensure_ascii=False,separators=(",",":"))
        entry=purchase.entry_snapshot
        with self.connect() as c:
            c.execute("""INSERT INTO virtual_purchases(
                virtual_purchase_id,opportunity_id,opportunity_observed_at,asin,jan,product_name,
                created_at,entry_price,expected_sale_price,expected_profit_yen,expected_roi,
                opportunity_score,urgency_score,confidence,status,quantity,snapshot_json,outcome_json,summary_json
                ,fee_source,fee_model_version,referral_fee,fulfillment_fee,total_fees
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(virtual_purchase_id) DO UPDATE SET
                status=excluded.status,outcome_json=excluded.outcome_json,summary_json=excluded.summary_json""",(
                purchase.virtual_purchase_id,purchase.opportunity_id,entry.opportunity_observed_at,
                purchase.asin,purchase.jan,purchase.product_name,purchase.created_at,entry.entry_price,
                entry.expected_sale_price,entry.expected_profit_yen,entry.expected_roi,
                entry.opportunity_score,entry.urgency_score,entry.confidence,purchase.status.value,
                purchase.quantity,snapshot,outcome,summary,
                entry.fee_source,entry.fee_model_version,entry.referral_fee,
                entry.fulfillment_fee,entry.total_fees,
            ))
            for observation in purchase.observations:
                c.execute("""INSERT OR IGNORE INTO virtual_purchase_observations(
                    virtual_purchase_id,observed_at,observed_price,sales_rank,new_offer_count,amazon_owned,data_quality
                ) VALUES(?,?,?,?,?,?,?)""",(
                    observation.virtual_purchase_id,observation.observed_at,observation.observed_price,
                    observation.sales_rank,observation.new_offer_count,
                    None if observation.amazon_owned is None else int(observation.amazon_owned),observation.data_quality,
                ))
    def load_virtual_purchases(self,status=None):
        from app.virtual_purchases.models import EntrySnapshot, FollowUpObservation, VirtualPurchase, VirtualPurchaseOutcome, VirtualPurchaseStatus, VirtualPurchaseSummary
        where=" WHERE status=?" if status is not None else ""
        params=(status.value if hasattr(status,"value") else status,) if status is not None else ()
        with self.connect() as c:
            rows=c.execute("SELECT * FROM virtual_purchases"+where+" ORDER BY created_at,virtual_purchase_id",params).fetchall()
            result=[]
            for row in rows:
                snapshot_data=json.loads(row["snapshot_json"])
                snapshot_data["signal_types"]=tuple(snapshot_data.get("signal_types") or ())
                snapshot_data["reasons"]=tuple(snapshot_data.get("reasons") or ())
                snapshot_data["risks"]=tuple(snapshot_data.get("risks") or ())
                # v5 snapshots predate fee versioning. Their calculation was the
                # fixed 10% + 450 yen DEFAULT_ESTIMATE model.
                snapshot_data.setdefault("referral_rate",0.10)
                snapshot_data.setdefault("referral_fee",row["referral_fee"] if row["referral_fee"] is not None else float(row["expected_sale_price"])*0.10)
                snapshot_data.setdefault("fulfillment_fee",row["fulfillment_fee"] if row["fulfillment_fee"] is not None else 450.0)
                snapshot_data.setdefault("shipping_cost",0.0)
                snapshot_data.setdefault("other_cost",0.0)
                snapshot_data.setdefault("total_fees",row["total_fees"] if row["total_fees"] is not None else snapshot_data["referral_fee"]+snapshot_data["fulfillment_fee"])
                snapshot_data.setdefault("fee_source",row["fee_source"])
                snapshot_data.setdefault("fee_model_version",row["fee_model_version"])
                snapshot_data.setdefault("fee_calculated_at",row["created_at"])
                outcome_data=json.loads(row["outcome_json"])
                outcome_data["outcome_status"]=VirtualPurchaseStatus(outcome_data["outcome_status"])
                summary_data=json.loads(row["summary_json"])
                summary_data["signal_types"]=tuple(summary_data.get("signal_types") or ())
                summary_data["status"]=VirtualPurchaseStatus(summary_data["status"])
                observations=tuple(FollowUpObservation(
                    item["virtual_purchase_id"],item["observed_at"],item["observed_price"],
                    item["sales_rank"],item["new_offer_count"],
                    None if item["amazon_owned"] is None else bool(item["amazon_owned"]),item["data_quality"],
                ) for item in c.execute("SELECT * FROM virtual_purchase_observations WHERE virtual_purchase_id=? ORDER BY observed_at",(row["virtual_purchase_id"],)))
                result.append(VirtualPurchase(
                    row["virtual_purchase_id"],row["opportunity_id"],row["asin"],row["jan"],row["product_name"],
                    row["created_at"],row["quantity"],VirtualPurchaseStatus(row["status"]),EntrySnapshot(**snapshot_data),
                    observations,VirtualPurchaseOutcome(**outcome_data),VirtualPurchaseSummary(**summary_data),
                ))
        return result
