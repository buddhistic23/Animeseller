"""eBay price lookups.

Primary path: the official Browse API (client-credentials OAuth), which returns
active fixed-price listings and supports GTIN (=ISBN-13) lookups so we get exact
matches instead of fuzzy title searches.

Fallback path: read eBay's public search results page. Used for ACTIVE
listings whenever no API keys are configured, and for SOLD listings (real
sell-through prices, which the Browse API doesn't expose) when either no keys
are configured or EBAY_SOLD_SCRAPE=1. Note this is against eBay's terms of
use; the Browse API path is the compliant one.
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
from .fetch import Fetcher
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


def parse_search_html(html: str, sold: bool = False) -> list[EbayListing]:
    """Parse eBay's search results page. Handles the classic `.s-item` cards,
    the 2024 `su-card` layout and the 2025 `s-card` layout."""
    tree = HTMLParser(html)
    out: list[EbayListing] = []
    seen: set[str] = set()
    cards = tree.css(".s-item, .su-card-container, .s-card, li[data-listingid]")
    for card in cards:
        title_el = card.css_first(
            ".s-item__title, .s-card__title, .su-styled-text.primary, [class*=__title], [class*=title]"
        )
        price_el = card.css_first(
            ".s-item__price, .s-card__price, .su-styled-text.positive, [class*=__price], [class*=price]"
        )
        link_el = card.css_first("a.s-item__link, a.s-card__link, a[href*='/itm/']")
        if title_el is None or price_el is None:
            continue
        title = title_el.text(strip=True)
        if not title or title.lower().startswith("shop on ebay"):
            continue
        price = _money(price_el.text())
        if price is None:
            continue
        ship_el = card.css_first(
            ".s-item__shipping, .s-item__logisticsCost, .s-card__shipping, [class*=shipping], [class*=logistics]"
        )
        ship = 0.0
        if ship_el is not None:
            ship = _money(ship_el.text()) or 0.0
        url = link_el.attributes.get("href", "") if link_el is not None else ""
        m = re.search(r"/itm/(\d+)", url)
        item_id = m.group(1) if m else (card.attributes.get("data-listingid") or "")
        if item_id and item_id in seen:
            continue
        seen.add(item_id)
        out.append(EbayListing(
            item_id=item_id, title=title, price=price, shipping=ship, url=url.split("?")[0], sold=sold,
        ))
    return out


# Backwards-compatible name.
def parse_sold_html(html: str) -> list[EbayListing]:
    return parse_search_html(html, sold=True)


class EbayClient:
    def __init__(self, s: Settings = default_settings, client: Optional[httpx.AsyncClient] = None):
        self.s = s
        self._client = client
        self._own = client is None
        self._token: Optional[str] = None
        self._token_exp: float = 0
        self._fetcher: Optional[Fetcher] = None

    async def __aenter__(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30, follow_redirects=True)
        self._fetcher = Fetcher(self.s)
        await self._fetcher.__aenter__()
        return self

    async def __aexit__(self, *exc):
        if self._own and self._client is not None:
            await self._client.aclose()
        if self._fetcher is not None:
            await self._fetcher.__aexit__(*exc)

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
        if not listings and isbn:
            listings = await self._browse({"q": isbn, "filter": base_filter, "limit": 50})
        if not listings and not isbn and title:
            listings = await self._browse({"q": title[:80], "filter": base_filter, "limit": 50})
        return listings

    def search_page_url(self, query: str, sold: bool) -> str:
        params = {"_nkw": query, "LH_ItemCondition": 1000, "LH_BIN": 1, "_ipg": 60, "LH_PrefLoc": 1}
        if sold:
            params.update({"LH_Sold": 1, "LH_Complete": 1, "_sop": 13})
            params.pop("LH_BIN")
        return f"https://www.ebay.com/sch/i.html?{urlencode(params)}"

    async def scrape_listings(self, query: str, sold: bool) -> list[EbayListing]:
        assert self._fetcher is not None
        html = await self._fetcher.get(self.search_page_url(query, sold))
        return parse_search_html(html, sold=sold)

    async def sold_listings(self, query: str) -> list[EbayListing]:
        return await self.scrape_listings(query, sold=True)

    async def comps(self, isbn: str, title: Optional[str] = None) -> EbayComps:
        comps = EbayComps(isbn=isbn or (title or ""))
        errors = []
        if not isbn and not title:
            comps.error = "no ISBN or title to search"
            return comps
        query = isbn or (title or "")[:80]
        use_api = self.s.ebay_configured
        if use_api:
            try:
                comps.active = await self.active_listings(isbn, title)
            except Exception as e:  # noqa: BLE001
                errors.append(f"browse: {e}")
        elif self.s.ebay_scrape:
            try:
                comps.active = await self.scrape_listings(query, sold=False)
            except Exception as e:  # noqa: BLE001
                errors.append(f"active scrape: {e}")
        else:
            errors.append("EBAY_CLIENT_ID/SECRET not set and EBAY_SCRAPE=0")
        want_sold = self.s.ebay_sold_scrape or (not use_api and self.s.ebay_scrape)
        if want_sold:
            try:
                comps.sold = await self.scrape_listings(query, sold=True)
            except Exception as e:  # noqa: BLE001
                errors.append(f"sold scrape: {e}")
        if errors:
            comps.error = "; ".join(errors)
        return comps
