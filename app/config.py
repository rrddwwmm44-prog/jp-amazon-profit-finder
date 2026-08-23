from __future__ import annotations
from dataclasses import dataclass, field
import os
from pathlib import Path

def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists(): return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

def _env_bool(name: str, default: bool) -> bool:
    value=os.getenv(name)
    if value is None: return default
    normalized=value.strip().lower()
    if normalized in {"1","true","yes","on"}: return True
    if normalized in {"0","false","no","off"}: return False
    raise ValueError(f"{name} must be true or false")

@dataclass(frozen=True)
class FeatureFlags:
    market_price: bool = True
    amazon_arbitrage: bool = False
    seller_decline: bool = False
    virtual_purchase: bool = False
    discontinued: bool = False
    supply_shortage: bool = False
    catalog_gap: bool = False
    sibling_search: bool = False
    sales_management: bool = False

    @classmethod
    def load(cls) -> "FeatureFlags":
        return cls(**{
            name: _env_bool(f"ENGINE_{name.upper()}_ENABLED", default)
            for name, default in cls().__dict__.items()
        })

    def is_enabled(self, name: str) -> bool:
        if name not in self.__dict__: raise KeyError(f"unknown feature flag: {name}")
        return bool(getattr(self,name))

    def items(self):
        return tuple(self.__dict__.items())

@dataclass(frozen=True)
class Settings:
    db_path: Path
    min_profit_yen: int
    min_profit_margin: float
    today_score_threshold: int
    log_level: str
    engine_flags: FeatureFlags = field(default_factory=FeatureFlags)
    keepa_enabled: bool = False
    keepa_cache_ttl_seconds: int = 21600
    min_arbitrage_profit_yen: int = 1000
    min_arbitrage_roi: float = 0.20
    min_arbitrage_drop_rate: float = 0.15
    max_arbitrage_sales_rank: int = 150000
    max_arbitrage_seller_count: int = 15
    min_seller_decline_rate_30d: float = 0.30
    min_seller_decline_score: int = 60

    @classmethod
    def load(cls) -> "Settings":
        _load_dotenv()
        keepa_key_set=bool(os.getenv("KEEPA_API_KEY"))
        return cls(Path(os.getenv("APP_DB_PATH", "data/profit_finder.db")), int(os.getenv("MIN_PROFIT_YEN", "500")), float(os.getenv("MIN_PROFIT_MARGIN", "0.15")), int(os.getenv("TODAY_SCORE_THRESHOLD", "85")), os.getenv("APP_LOG_LEVEL", "INFO"), FeatureFlags.load(), _env_bool("KEEPA_ENABLED",keepa_key_set), int(os.getenv("KEEPA_CACHE_TTL_SECONDS","21600")), int(os.getenv("MIN_ARBITRAGE_PROFIT_YEN","1000")), float(os.getenv("MIN_ARBITRAGE_ROI","0.20")), float(os.getenv("MIN_ARBITRAGE_DROP_RATE","0.15")), int(os.getenv("MAX_ARBITRAGE_SALES_RANK","150000")), int(os.getenv("MAX_ARBITRAGE_SELLER_COUNT","15")), float(os.getenv("MIN_SELLER_DECLINE_RATE_30D","0.30")), int(os.getenv("MIN_SELLER_DECLINE_SCORE","60")))
