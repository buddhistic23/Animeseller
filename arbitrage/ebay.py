"""eBay price lookups.

Primary path: the official Browse API (client-credentials OAuth), which returns
active fixed-price listings and supports GTIN (=ISBN-13) lookups so we get exact
matches instead of fuzzy title searches.

Optional path: scrape eBay's public search page for SOLD listings. That gives
real sell-through prices, which the Browse API doesn't expose without the
restricted Marketplace Insights API. It's off by default (EBAY_SOLD_SCRAPE=1).
"""
from __future__ import annotations

import base64
import re
import time
from typing import Optional
from urllib.parse import urlencode

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .config import Settings, settings as default_settings
from .models import EbayComps, EbayListing

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
SCOPE = "https://api.ebay.com/oauth/api_scope"
PRICE_RE = re.compile(r"\$\s*(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)")


def _money(text: str) -> Optional[float]:
    m = PRICE_RE.search(text or "")
    return float(m.group(1).replace(",", "")) if m else None


def parse_browse_response(data: dict) -> list[EbayListing]:
    out: list[EbayListing] = []
    for it in data.get("itemSummaries", []) or []:
        try:
            price = float(it["price"]["value"])
        except (KeyError, TypeError, ValueError):
            continue
        ship = 0.0
        opts = it.get("shippingOptions") or []
        if opts:
            sc = (opts[0].get("shippingCost") or {}).get("value")
            try:
                ship = float(sc) if sc is not None else 0.0
            except (TypeError, ValueError):
                ship = 0.0
        out.append(EbayListing(
            item_id=str(it.get("itemId", "")),
            title=it.get("title", ""),
            price=price,
            shipping=ship,
            condition=it.get("condition"),
            url=it.get("itemWebUrl", ""),
        ))
    return out


def parse_sold_html(html: str) -> list[EbayListing]:
    """Parse eBay's search results page. Handles the classic `.s-item` cards and
    the newer `su-card` layout."""
    tree = HTMLParser(html)
    out: list[EbayListing] = []
    cards = tree.css(".s-item, .su-card-container")
    for card in cards:
        title_el = card.css_first(".s-item__title, .su-styled-text.primary, [class*=title]")
        price_el = card.css_first(".s-item__price, .su-styled-text.positive, [class*=price]")
        link_el = card.css_first("a.s-item__link, a[href*='/itm/']")
        if title_el is None or price_el is None:
            continue
        title = title_el.text(strip=True)
        if not title or title.lower().startswith("shop on ebay"):
            continue
        price = _money(price_el.text())
        if price is None:
            continue
        ship_el = card.css_first(".s-item__shipping, .s-item__logisticsCost, [class*=shipping]")
        ship = 0.0
        if ship_el is not None:
            ship = _money(ship_el.text()) or 0.0
        url = link_el.attributes.get("href", "") if link_el is not None else ""
        m = re.search(r"/itm/(\d+)", url)
        out.append(EbayListing(
            item_id=m.group(1) if m else "",
            title=title, price=price, shipping=ship, url=url, sold=True,
        ))
    return out


class EbayClient:
    def __init__(self, s: Settings = default_settings, client: Optional[httpx.AsyncClient] = None):
        self.s = s
        self._client = client
        self._own = client is None
        self._token: Optional[str] = None
        self._token_exp: float = 0

    async def __aenter__(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30, follow_redirects=True)
        return self

    async def __aexit__(self, *exc):
        if self._own and self._client is not None:
            await self._client.aclose()

    async def _get_token(self) -> str:
        assert self._client is not None
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        basic = base64.b64encode(f"{self.s.ebay_client_id}:{self.s.ebay_client_secret}".encode()).decode()
        r = await self._client.post(
            TOKEN_URL,
            headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": SCOPE},
        )
        r.raise_for_status()
        data = r.json()
        self._token = data["access_token"]
        self._token_exp = time.time() + int(data.get("expires_in", 7200))
        return self._token

    async def _browse(self, params: dict) -> list[EbayListing]:
        assert self._client is not None
        token = await self._get_token()
        r = await self._client.get(
            BROWSE_URL,
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": self.s.ebay_marketplace_id,
                "Accept": "application/json",
            },
        )
        r.raise_for_status()
        return parse_browse_response(r.json())

    async def active_listings(self, isbn: str, title: Optional[str] = None) -> list[EbayListing]:
        base_filter = "buyingOptions:{FIXED_PRICE},conditions:{NEW},deliveryCountry:US,itemLocationCountry:US"
        # GTIN lookup = exact ISBN-13 match. Fall back to keyword search on the ISBN.
        listings: list[EbayListing] = []
        if len(isbn) == 13:
            listings = await self._browse({"gtin": isbn, "filter": base_filter, "limit": 50})
        if not listings:
            listings = await self._browse({"q": isbn, "filter": base_filter, "limit": 50})
        return listings

    async def sold_listings(self, isbn: str) -> list[EbayListing]:
        assert self._client is not None
        q = urlencode({"_nkw": isbn, "LH_Sold": 1, "LH_Complete": 1, "LH_ItemCondition": 1000, "_ipg": 60})
        r = await self._client.get(
            f"https://www.ebay.com/sch/i.html?{q}",
            headers={"User-Agent": self.s.user_agent, "Accept-Language": "en-US,en;q=0.9"},
        )
        r.raise_for_status()
        return parse_sold_html(r.text)

    async def comps(self, isbn: str, title: Optional[str] = None) -> EbayComps:
        comps = EbayComps(isbn=isbn)
        errors = []
        if self.s.ebay_configured:
            try:
                comps.active = await self.active_listings(isbn, title)
            except Exception as e:  # noqa: BLE001
                errors.append(f"browse: {e}")
        else:
            errors.append("EBAY_CLIENT_ID/SECRET not set")
        if self.s.ebay_sold_scrape:
            try:
                comps.sold = await self.sold_listings(isbn)
            except Exception as e:  # noqa: BLE001
                errors.append(f"sold: {e}")
        if errors:
            comps.error = "; ".join(errors)
        return comps
