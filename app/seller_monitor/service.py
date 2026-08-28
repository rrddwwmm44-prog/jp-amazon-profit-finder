from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol

from app.providers.keepa import KeepaSellerResult, KeepaTokensExhausted
from app.services.keepa_budget import build_keepa_budget
from app.storage.db import Database


class SellerStorefrontProvider(Protocol):
    def get_seller_storefront(self, seller_id: str) -> KeepaSellerResult: ...


@dataclass(frozen=True)
class SellerCheckResult:
    seller_id: str
    observation_type: str
    current_asin_count: int
    new_count: int
    checked_at: str

    def to_dict(self) -> dict:
        return asdict(self)


class SellerMonitorService:
    def __init__(self, db: Database, provider: SellerStorefrontProvider | None = None,
                 budget_builder: Callable = build_keepa_budget):
        self.db = db
        self.provider = provider
        self.budget_builder = budget_builder

    @staticmethod
    def normalize_seller_id(seller_id: str) -> str:
        value = seller_id.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{10,20}", value):
            raise ValueError("invalid Seller ID")
        return value

    def add(self, seller_id: str, seller_name: str | None = None, memo: str | None = None) -> dict:
        seller_id = self.normalize_seller_id(seller_id)
        now = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as c:
            c.execute("""INSERT INTO seller_monitors(seller_id,seller_name,memo,enabled,created_at,updated_at)
                         VALUES(?,?,?,1,?,?)
                         ON CONFLICT(seller_id) DO UPDATE SET
                         seller_name=excluded.seller_name,memo=excluded.memo,updated_at=excluded.updated_at""",
                      (seller_id, seller_name, memo, now, now))
        return self.get(seller_id)

    def set_enabled(self, seller_id: str, enabled: bool) -> dict:
        seller_id = self.normalize_seller_id(seller_id)
        with self.db.connect() as c:
            changed = c.execute("UPDATE seller_monitors SET enabled=?,updated_at=? WHERE seller_id=?",
                                (int(enabled), datetime.now(timezone.utc).isoformat(), seller_id)).rowcount
        if not changed:
            raise ValueError("seller is not registered")
        return self.get(seller_id)

    def get(self, seller_id: str) -> dict:
        with self.db.connect() as c:
            row = c.execute("SELECT * FROM seller_monitors WHERE seller_id=?", (seller_id,)).fetchone()
        if row is None:
            raise ValueError("seller is not registered")
        result = dict(row); result["enabled"] = bool(result["enabled"])
        return result

    def list_sellers(self) -> list[dict]:
        with self.db.connect() as c:
            rows = c.execute("SELECT * FROM seller_monitors ORDER BY seller_id").fetchall()
        result = [dict(row) for row in rows]
        for item in result: item["enabled"] = bool(item["enabled"])
        return result

    def list_new(self, seller_id: str | None = None) -> list[dict]:
        params = () if seller_id is None else (self.normalize_seller_id(seller_id),)
        where = "" if seller_id is None else " WHERE seller_id=?"
        with self.db.connect() as c:
            return [dict(row) for row in c.execute(
                "SELECT asin,source_type,seller_id,detected_at FROM seller_monitor_detections" + where +
                " ORDER BY detected_at DESC,id DESC", params)]

    @staticmethod
    def budget_allows_storefront(budget: dict) -> bool:
        if budget.get("status", "UNKNOWN") in {"CRITICAL", "EXHAUSTED"}:
            return False
        tokens_left = budget.get("tokens_left")
        return not isinstance(tokens_left, int) or tokens_left >= 10

    def check(self, seller_id: str, *, now: datetime | None = None,
              enforce_budget: bool = True) -> SellerCheckResult:
        seller_id = self.normalize_seller_id(seller_id)
        monitor = self.get(seller_id)
        if not monitor["enabled"]:
            raise ValueError("seller is disabled")
        if self.provider is None:
            raise ValueError("Keepa provider is required")
        now = now or datetime.now(timezone.utc)
        if enforce_budget and not self.budget_allows_storefront(self.budget_builder(self.db, now)):
            raise ValueError("Keepa budget does not allow a storefront request")
        observed_at = now.isoformat()
        storefront = self.provider.get_seller_storefront(seller_id)
        asins = set(storefront.asins)
        with self.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            initial = monitor["last_checked_at"] is None
            known = {row[0] for row in c.execute("SELECT asin FROM seller_monitor_asins WHERE seller_id=?", (seller_id,))}
            new_asins = set() if initial else asins - known
            c.execute("UPDATE seller_monitor_asins SET is_current=0 WHERE seller_id=?", (seller_id,))
            for asin in sorted(asins):
                status = "BASELINE" if initial else "NEW"
                c.execute("""INSERT INTO seller_monitor_asins(seller_id,asin,status,first_seen_at,last_seen_at,is_current)
                             VALUES(?,?,?,?,?,1)
                             ON CONFLICT(seller_id,asin) DO UPDATE SET last_seen_at=excluded.last_seen_at,is_current=1""",
                          (seller_id, asin, status, observed_at, observed_at))
            for asin in sorted(new_asins):
                c.execute("INSERT OR IGNORE INTO seller_monitor_detections(asin,source_type,seller_id,detected_at) VALUES(?,'seller_monitor',?,?)",
                          (asin, seller_id, observed_at))
            name = monitor["seller_name"] or storefront.seller_name
            c.execute("""UPDATE seller_monitors SET seller_name=?,last_checked_at=?,current_asin_count=?,
                         last_new_count=?,updated_at=? WHERE seller_id=?""",
                      (name, observed_at, len(asins), len(new_asins), observed_at, seller_id))
        return SellerCheckResult(seller_id, "BASELINE" if initial else "CHECK", len(asins), len(new_asins), observed_at)

    def check_enabled(self, *, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        budget = self.budget_builder(self.db, now)
        sellers = [item for item in self.list_sellers() if item["enabled"]]
        status = budget.get("status", "UNKNOWN")
        # A storefront request costs up to 10 tokens. Bootstrap conservatively
        # until the shared budget manager has enough observations.
        limit = len(sellers) if status == "HEALTHY" else 1 if status in {"LIMITED", "UNKNOWN"} else 0
        tokens_left = budget.get("tokens_left")
        if isinstance(tokens_left, int):
            limit = min(limit, max(0, tokens_left // 10))
        results=[]
        for seller in sellers[:limit]:
            try:
                results.append(self.check(seller["seller_id"], now=now, enforce_budget=False).to_dict())
            except KeepaTokensExhausted:
                break
        return {"budget_status": status, "eligible": len(sellers), "planned": limit,
                "checked": len(results), "results": results}
