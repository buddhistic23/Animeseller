from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _f(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return float(v)


def _i(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return int(v)


def _b(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    ebay_client_id: str = field(default_factory=lambda: os.getenv("EBAY_CLIENT_ID", ""))
    ebay_client_secret: str = field(default_factory=lambda: os.getenv("EBAY_CLIENT_SECRET", ""))
    ebay_marketplace_id: str = field(default_factory=lambda: os.getenv("EBAY_MARKETPLACE_ID", "EBAY_US"))
    ebay_sold_scrape: bool = field(default_factory=lambda: _b("EBAY_SOLD_SCRAPE"))

    kino_base_url: str = field(
        default_factory=lambda: os.getenv("KINO_BASE_URL", "https://united-states.kinokuniya.com").rstrip("/")
    )
    kino_member_discount: float = field(default_factory=lambda: _f("KINO_MEMBER_DISCOUNT", 0.10))
    kino_sales_tax: float = field(default_factory=lambda: _f("KINO_SALES_TAX", 0.0))
    kino_shipping_per_item: float = field(default_factory=lambda: _f("KINO_SHIPPING_PER_ITEM", 0.0))

    ebay_fvf_rate: float = field(default_factory=lambda: _f("EBAY_FVF_RATE", 0.1325))
    ebay_per_order_fee: float = field(default_factory=lambda: _f("EBAY_PER_ORDER_FEE", 0.30))
    ebay_ship_cost: float = field(default_factory=lambda: _f("EBAY_SHIP_COST", 5.00))
    buyer_tax_rate: float = field(default_factory=lambda: _f("BUYER_TAX_RATE", 0.07))

    mock_mode: bool = field(default_factory=lambda: _b("MOCK_MODE"))
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "data/arbitrage.db"))
    request_delay: float = field(default_factory=lambda: _f("REQUEST_DELAY_SECONDS", 1.5))
    max_items_per_scan: int = field(default_factory=lambda: _i("MAX_ITEMS_PER_SCAN", 60))

    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    @property
    def ebay_configured(self) -> bool:
        return bool(self.ebay_client_id and self.ebay_client_secret)


settings = Settings()
