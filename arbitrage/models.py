from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class KinoProduct(BaseModel):
    isbn: str = ""  # ISBN-13 from the Shopify barcode field; may be empty for merch
    handle: Optional[str] = None  # Shopify product handle
    title: str
    url: str
    price: float  # list price at Kinokuniya, USD
    sale_price: Optional[float] = None  # if marked down
    image: Optional[str] = None
    author: Optional[str] = None
    in_stock: bool = True

    @property
    def effective_price(self) -> float:
        return self.sale_price if self.sale_price is not None else self.price

    @property
    def key(self) -> str:
        """Stable id for caching/results: ISBN when we have one, else the handle."""
        return self.isbn or f"handle:{self.handle or self.url}"


class EbayListing(BaseModel):
    item_id: str
    title: str
    price: float
    shipping: float = 0.0
    condition: Optional[str] = None
    url: str
    sold: bool = False  # True when the listing came from sold/completed comps


class EbayComps(BaseModel):
    isbn: str
    active: list[EbayListing] = Field(default_factory=list)
    sold: list[EbayListing] = Field(default_factory=list)
    active_min: Optional[float] = None  # lowest active price incl. shipping
    active_median: Optional[float] = None
    sold_median: Optional[float] = None
    sold_count: int = 0
    active_count: int = 0
    error: Optional[str] = None


class Opportunity(BaseModel):
    product: KinoProduct
    comps: EbayComps
    cost_basis: float  # what you pay Kinokuniya, all in
    target_price: Optional[float]  # price we assume you can sell at
    target_source: str  # "sold_median" | "active_min" | "none"
    ebay_fees: Optional[float]
    net_profit: Optional[float]
    margin_pct: Optional[float]  # net_profit / target_price
    roi_pct: Optional[float]  # net_profit / cost_basis
    verdict: str  # "buy" | "maybe" | "pass" | "unknown"


class ScanRequest(BaseModel):
    keyword: Optional[str] = None
    url: Optional[str] = None  # any Kinokuniya listing/search URL
    isbns: Optional[list[str]] = None  # paste a list of ISBNs directly
    max_items: Optional[int] = None
    member: bool = True  # apply Privilege Card discount
    min_roi: float = 0.0  # filter in UI, but stored on scan for reference


class ScanStatus(BaseModel):
    id: int
    status: str  # queued | running | done | error
    source: str
    total: int = 0
    done: int = 0
    message: str = ""
    created_at: str = ""
