"""Kinokuniya USA client.

usa.kinokuniya.com is a Shopify store, so instead of scraping prices out of
HTML we use Shopify's standard storefront endpoints:

  /search?q=<term>&type=product&page=N      HTML; we only pull product handles from it
  /collections/<handle>/products.json       JSON list of a collection's products
  /products/<handle>.js                     JSON for one product: price, stock, barcode (= ISBN)
  /search/suggest.json?q=<isbn>&...         predictive search, lets us look up by barcode

Prices in .js are integer cents. Pages are fetched through Fetcher so bot
protection is handled in one place.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Iterable, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .config import Settings, settings as default_settings
from .fetch import Fetcher
from .models import KinoProduct

HANDLE_RE = re.compile(r"/products/([a-z0-9][a-z0-9\-_.%]*?)(?:\.js|\.json)?(?:[/?#]|$)", re.I)
ISBN_RE = re.compile(r"\b(97[89]\d{10})\b")
COLLECTION_RE = re.compile(r"/collections/([a-z0-9][a-z0-9\-_.%]*)", re.I)


def _cents(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return round(int(v) / 100, 2)
    except (TypeError, ValueError):
        try:
            return round(float(v), 2)
        except (TypeError, ValueError):
            return None


def _isbn_from(*candidates: Optional[str]) -> Optional[str]:
    for c in candidates:
        if not c:
            continue
        digits = re.sub(r"[^0-9Xx]", "", str(c))
        if re.fullmatch(r"97[89]\d{10}", digits):
            return digits
        m = ISBN_RE.search(str(c))
        if m:
            return m.group(1)
    return None


def _clean_title(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


def product_from_js(data: dict, base_url: str) -> Optional[KinoProduct]:
    """Build a KinoProduct from Shopify's /products/<handle>.js payload."""
    if not isinstance(data, dict) or not data.get("handle"):
        return None
    variants = data.get("variants") or []
    # Prefer an available variant; otherwise the first.
    v = next((x for x in variants if x.get("available")), variants[0] if variants else {})
    price = _cents(v.get("price", data.get("price")))
    if price is None:
        return None
    compare = _cents(v.get("compare_at_price") or data.get("compare_at_price"))
    list_price, sale_price = price, None
    if compare and compare > price:
        list_price, sale_price = compare, price
    isbn = _isbn_from(v.get("barcode"), v.get("sku"), data.get("handle"), data.get("title"))
    img = data.get("featured_image") or (data.get("images") or [None])[0]
    if isinstance(img, dict):
        img = img.get("src")
    if img and img.startswith("//"):
        img = "https:" + img
    return KinoProduct(
        isbn=isbn or "",
        handle=data["handle"],
        title=_clean_title(data.get("title", "")) or "Untitled",
        url=urljoin(base_url, f"/products/{data['handle']}"),
        price=list_price,
        sale_price=sale_price,
        image=img,
        author=data.get("vendor") or None,
        in_stock=bool(v.get("available", data.get("available", True))),
    )


def product_from_listing_json(data: dict, base_url: str) -> Optional[KinoProduct]:
    """products.json entries: prices are decimal strings and there is no barcode."""
    if not isinstance(data, dict) or not data.get("handle"):
        return None
    variants = data.get("variants") or []
    v = next((x for x in variants if x.get("available")), variants[0] if variants else {})
    try:
        price = round(float(v.get("price")), 2)
    except (TypeError, ValueError):
        return None
    compare = None
    try:
        compare = round(float(v.get("compare_at_price")), 2) if v.get("compare_at_price") else None
    except (TypeError, ValueError):
        pass
    list_price, sale_price = price, None
    if compare and compare > price:
        list_price, sale_price = compare, price
    img = (data.get("images") or [{}])[0].get("src") if data.get("images") else None
    return KinoProduct(
        isbn=_isbn_from(v.get("sku"), data.get("handle"), data.get("title")) or "",
        handle=data["handle"],
        title=_clean_title(data.get("title", "")) or "Untitled",
        url=urljoin(base_url, f"/products/{data['handle']}"),
        price=list_price, sale_price=sale_price, image=img,
        author=data.get("vendor") or None,
        in_stock=bool(v.get("available", True)),
    )


def handles_from_html(html: str) -> list[str]:
    """Product handles linked from a search/collection page, main content first."""
    tree = HTMLParser(html)
    root = tree.css_first("main, #MainContent, [role=main]") or tree.body or tree.root
    out: list[str] = []
    seen: set[str] = set()
    for a in root.css("a[href]"):
        m = HANDLE_RE.search(a.attributes.get("href") or "")
        if not m:
            continue
        h = m.group(1)
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def with_page(url: str, page: int) -> str:
    parts = urlparse(url)
    q = parse_qs(parts.query, keep_blank_values=True)
    q["page"] = [str(page)]
    return urlunparse(parts._replace(query=urlencode(q, doseq=True)))


class KinokuniyaClient:
    def __init__(self, s: Settings = default_settings, fetcher: Optional[Fetcher] = None):
        self.s = s
        self._fetcher = fetcher
        self._own = fetcher is None

    async def __aenter__(self):
        if self._fetcher is None:
            self._fetcher = Fetcher(self.s)
            await self._fetcher.__aenter__()
        return self

    async def __aexit__(self, *exc):
        if self._own and self._fetcher is not None:
            await self._fetcher.__aexit__(*exc)

    # ---- urls ----
    def search_url(self, keyword: str) -> str:
        return f"{self.s.kino_base_url}/search?{urlencode({'q': keyword, 'type': 'product', 'options[prefix]': 'last'})}"

    def product_js_url(self, handle: str) -> str:
        return f"{self.s.kino_base_url}/products/{handle}.js"

    # ---- fetch helpers ----
    async def _get(self, url: str) -> str:
        assert self._fetcher is not None
        return await self._fetcher.get(url)

    async def _get_json(self, url: str):
        text = await self._get(url)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    async def product_by_handle(self, handle: str) -> Optional[KinoProduct]:
        try:
            data = await self._get_json(self.product_js_url(handle))
        except RuntimeError as e:
            if "404" in str(e):
                return None
            raise
        return product_from_js(data, self.s.kino_base_url) if data else None

    async def _products_for_handles(self, handles: Iterable[str], max_items: int) -> list[KinoProduct]:
        out: list[KinoProduct] = []
        for h in handles:
            p = await self.product_by_handle(h)
            if p:
                out.append(p)
                if len(out) >= max_items:
                    break
            await asyncio.sleep(self.s.request_delay)
        return out

    async def _handles_from_pages(self, url: str, max_items: int) -> list[str]:
        handles: list[str] = []
        seen: set[str] = set()
        for page in range(1, 30):
            page_url = url if page == 1 else with_page(url, page)
            html = await self._get(page_url)
            new = [h for h in handles_from_html(html) if h not in seen]
            if not new:
                break
            for h in new:
                seen.add(h)
                handles.append(h)
            if len(handles) >= max_items:
                break
            await asyncio.sleep(self.s.request_delay)
        return handles[:max_items]

    # ---- public API ----
    async def search(self, keyword: str, max_items: int) -> list[KinoProduct]:
        handles = await self._handles_from_pages(self.search_url(keyword), max_items)
        return await self._products_for_handles(handles, max_items)

    async def listing(self, url: str, max_items: int) -> list[KinoProduct]:
        """Any usa.kinokuniya.com page: a collection, a search, or a single product."""
        m = HANDLE_RE.search(urlparse(url).path)
        if m:
            p = await self.product_by_handle(m.group(1))
            return [p] if p else []
        cm = COLLECTION_RE.search(urlparse(url).path)
        if cm:
            handles: list[str] = []
            for page in range(1, 30):
                data = await self._get_json(
                    f"{self.s.kino_base_url}/collections/{cm.group(1)}/products.json?limit=250&page={page}"
                )
                items = (data or {}).get("products") or []
                if not items:
                    break
                handles.extend(i.get("handle") for i in items if i.get("handle"))
                if len(handles) >= max_items or len(items) < 250:
                    break
                await asyncio.sleep(self.s.request_delay)
            return await self._products_for_handles(handles[:max_items], max_items)
        handles = await self._handles_from_pages(url, max_items)
        return await self._products_for_handles(handles, max_items)

    async def product(self, isbn: str) -> Optional[KinoProduct]:
        """Look a product up by ISBN via predictive search on the barcode field."""
        q = urlencode({
            "q": isbn, "resources[type]": "product", "resources[limit]": 5,
            "resources[options][fields]": "variants.barcode,variants.sku,title",
            "resources[options][unavailable_products]": "last",
        })
        data = await self._get_json(f"{self.s.kino_base_url}/search/suggest.json?{q}")
        products = (((data or {}).get("resources") or {}).get("results") or {}).get("products") or []
        for item in products:
            h = item.get("handle")
            if not h:
                continue
            p = await self.product_by_handle(h)
            if p and (p.isbn == isbn or len(products) == 1):
                return p
        # Fall back to the HTML search page.
        handles = await self._handles_from_pages(self.search_url(isbn), 3)
        for h in handles:
            p = await self.product_by_handle(h)
            if p and p.isbn == isbn:
                return p
        return None

    async def products(self, isbns: Iterable[str]) -> list[KinoProduct]:
        out = []
        for isbn in isbns:
            p = await self.product(isbn)
            if p:
                out.append(p)
            await asyncio.sleep(self.s.request_delay)
        return out
