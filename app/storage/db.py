from __future__ import annotations
import json, sqlite3
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
