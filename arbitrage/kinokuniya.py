"""Kinokuniya USA scraper.

The site is server-rendered, so plain HTTP + HTML parsing is enough. Markup
changes over time, so the parser is deliberately loose: it keys off the one
stable thing (product links look like /products/<ISBN13>) and pulls the
price/title from the nearest enclosing card.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Iterable, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser, LexborNode as Node

from .config import Settings, settings as default_settings
from .models import KinoProduct

PRODUCT_LINK_RE = re.compile(r"/products/(\d{13}|\d{10})(?:[/?#]|$)")
PRICE_RE = re.compile(r"(?:US\s*)?\$\s*(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)")
ISBN_RE = re.compile(r"\b(97[89]\d{10}|\d{9}[\dXx])\b")
STRIKE_CLASSES = ("strike", "line-through", "old", "list-price", "regular", "was", "original")


def _price(text: str) -> Optional[float]:
    m = PRICE_RE.search(text or "")
    if not m:
        return None
    return float(m.group(1).replace(",", ""))


def _all_prices(text: str) -> list[float]:
    return [float(m.group(1).replace(",", "")) for m in PRICE_RE.finditer(text or "")]


def _is_struck(node: Node) -> bool:
    n: Optional[Node] = node
    depth = 0
    while n is not None and depth < 4:
        if n.tag in ("s", "del", "strike"):
            return True
        cls = (n.attributes.get("class") or "").lower()
        style = (n.attributes.get("style") or "").lower()
        if any(c in cls for c in STRIKE_CLASSES) or "line-through" in style:
            return True
        n = n.parent
        depth += 1
    return False


def _card_for(anchor: Node) -> Node:
    """Walk up from a product link until the container holds a price."""
    n: Optional[Node] = anchor
    best = anchor
    for _ in range(8):
        if n is None:
            break
        text = n.text(separator=" ", strip=True)
        if PRICE_RE.search(text):
            return n
        best = n
        n = n.parent
    return best


def _clean(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _extract_title(anchor: Node, card: Node) -> str:
    for cand in (
        anchor.attributes.get("title"),
        anchor.text(strip=True),
    ):
        cand = _clean(cand)
        if cand and len(cand) > 2 and not PRICE_RE.fullmatch(cand):
            return cand
    img = anchor.css_first("img") or card.css_first("img")
    if img is not None:
        alt = _clean(img.attributes.get("alt"))
        if alt:
            return alt
    for sel in ("h1", "h2", "h3", "h4", ".title", "[class*=title]", "[class*=name]"):
        el = card.css_first(sel)
        if el is not None and _clean(el.text()):
            return _clean(el.text())
    return "Untitled"


def _extract_prices(card: Node) -> tuple[Optional[float], Optional[float]]:
    """Return (list_price, sale_price). sale_price is None when not discounted."""
    struck: list[float] = []
    normal: list[float] = []
    for el in card.css("*"):
        if el.tag in ("script", "style"):
            continue
        # Only leaf-ish nodes: skip elements whose own direct text has no price.
        own = "".join(c.text() for c in el.iter(include_text=True) if c.tag == "-text")
        prices = _all_prices(own)
        if not prices:
            continue
        (struck if _is_struck(el) else normal).extend(prices)
    if not struck and not normal:
        prices = _all_prices(card.text(separator=" "))
        if not prices:
            return None, None
        if len(prices) >= 2 and prices[0] > prices[1]:
            return prices[0], prices[1]
        return prices[0], None
    if struck and normal:
        return max(struck), min(normal)
    if normal:
        if len(normal) >= 2 and max(normal) > min(normal):
            return max(normal), min(normal)
        return normal[0], None
    return max(struck), None


def _in_stock(card: Node) -> bool:
    t = card.text(separator=" ", strip=True).lower()
    return not any(x in t for x in ("out of stock", "sold out", "unavailable", "not available"))


def parse_listing(html: str, base_url: str) -> list[KinoProduct]:
    tree = HTMLParser(html)
    seen: dict[str, KinoProduct] = {}
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        m = PRODUCT_LINK_RE.search(href)
        if not m:
            continue
        isbn = m.group(1)
        card = _card_for(a)
        list_price, sale_price = _extract_prices(card)
        title = _extract_title(a, card)
        existing = seen.get(isbn)
        # Several anchors (image + title) point at the same product; merge, prefer richer data.
        if existing is not None:
            if existing.price == 0 and list_price:
                existing.price = list_price
                existing.sale_price = sale_price
            if existing.title == "Untitled" and title != "Untitled":
                existing.title = title
            if not existing.image:
                img = card.css_first("img")
                if img is not None:
                    existing.image = urljoin(base_url, img.attributes.get("src") or img.attributes.get("data-src") or "")
            continue
        img = card.css_first("img")
        image = None
        if img is not None:
            src = img.attributes.get("src") or img.attributes.get("data-src") or ""
            image = urljoin(base_url, src) if src else None
        author = None
        for sel in ("[class*=author]", ".author", "[class*=Author]"):
            el = card.css_first(sel)
            if el is not None and _clean(el.text()):
                author = _clean(el.text())
                break
        seen[isbn] = KinoProduct(
            isbn=isbn,
            title=title,
            url=urljoin(base_url, href),
            price=list_price or 0.0,
            sale_price=sale_price,
            image=image,
            author=author,
            in_stock=_in_stock(card),
        )
    return [p for p in seen.values()]


def parse_product_page(html: str, url: str) -> Optional[KinoProduct]:
    tree = HTMLParser(html)
    isbn = None
    m = PRODUCT_LINK_RE.search(url)
    if m:
        isbn = m.group(1)

    title = None
    author = None
    price = None
    sale = None
    image = None

    # JSON-LD is the most reliable when present.
    for s in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(s.text())
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for d in items:
            if not isinstance(d, dict):
                continue
            if d.get("@type") in ("Product", "Book"):
                title = title or d.get("name")
                isbn = isbn or d.get("isbn") or d.get("gtin13") or d.get("gtin")
                image = image or (d.get("image") if isinstance(d.get("image"), str) else None)
                offers = d.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if isinstance(offers, dict) and offers.get("price"):
                    try:
                        price = float(offers["price"])
                    except (TypeError, ValueError):
                        pass
                a = d.get("author")
                if isinstance(a, dict):
                    author = a.get("name")
                elif isinstance(a, list) and a and isinstance(a[0], dict):
                    author = a[0].get("name")
                elif isinstance(a, str):
                    author = a

    if not title:
        og = tree.css_first('meta[property="og:title"]')
        if og is not None:
            title = og.attributes.get("content")
    if not title:
        h1 = tree.css_first("h1")
        if h1 is not None:
            title = h1.text(strip=True)
    if not title:
        t = tree.css_first("title")
        if t is not None:
            title = t.text(strip=True).split("|")[0]
    if not isbn:
        m2 = ISBN_RE.search(tree.body.text(separator=" ") if tree.body else html)
        isbn = m2.group(1) if m2 else None
    if not image:
        og = tree.css_first('meta[property="og:image"]')
        if og is not None:
            image = og.attributes.get("content")

    if price is None:
        body = tree.body if tree.body is not None else tree.root
        if body is not None:
            lp, sp = _extract_prices(body)
            price, sale = lp, sp

    if not isbn or price is None:
        return None
    return KinoProduct(
        isbn=isbn, title=_clean(title) or "Untitled", url=url, price=price, sale_price=sale,
        image=image, author=_clean(author) or None,
        in_stock=_in_stock(tree.body) if tree.body is not None else True,
    )


def with_page(url: str, page: int) -> str:
    parts = urlparse(url)
    q = parse_qs(parts.query, keep_blank_values=True)
    q["page"] = [str(page)]
    return urlunparse(parts._replace(query=urlencode(q, doseq=True)))


class KinokuniyaClient:
    def __init__(self, s: Settings = default_settings, client: Optional[httpx.AsyncClient] = None):
        self.s = s
        self._client = client
        self._own = client is None

    async def __aenter__(self):
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": self.s.user_agent, "Accept-Language": "en-US,en;q=0.9"},
                timeout=30, follow_redirects=True,
            )
        return self

    async def __aexit__(self, *exc):
        if self._own and self._client is not None:
            await self._client.aclose()

    def search_url(self, keyword: str) -> str:
        return f"{self.s.kino_base_url}/products?{urlencode({'keyword': keyword})}"

    def product_url(self, isbn: str) -> str:
        return f"{self.s.kino_base_url}/products/{isbn}"

    async def _get(self, url: str) -> str:
        assert self._client is not None
        r = await self._client.get(url)
        r.raise_for_status()
        return r.text

    async def listing(self, url: str, max_items: int) -> list[KinoProduct]:
        out: list[KinoProduct] = []
        seen: set[str] = set()
        for page in range(1, 20):
            page_url = url if page == 1 else with_page(url, page)
            html = await self._get(page_url)
            products = parse_listing(html, self.s.kino_base_url)
            new = [p for p in products if p.isbn not in seen]
            if not new:
                break
            for p in new:
                seen.add(p.isbn)
                out.append(p)
                if len(out) >= max_items:
                    return out
            await asyncio.sleep(self.s.request_delay)
        return out

    async def search(self, keyword: str, max_items: int) -> list[KinoProduct]:
        return await self.listing(self.search_url(keyword), max_items)

    async def product(self, isbn: str) -> Optional[KinoProduct]:
        url = self.product_url(isbn)
        try:
            html = await self._get(url)
        except httpx.HTTPStatusError:
            return None
        return parse_product_page(html, url)

    async def products(self, isbns: Iterable[str]) -> list[KinoProduct]:
        out = []
        for isbn in isbns:
            p = await self.product(isbn)
            if p:
                out.append(p)
            await asyncio.sleep(self.s.request_delay)
        return out
