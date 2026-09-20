"""Runs one scan: pull products from Kinokuniya, look up eBay comps for each,
score them, and stream results into the DB so the UI can poll progress."""
from __future__ import annotations

import asyncio
import logging
import re

from . import mock
from .config import Settings, settings as default_settings
from .db import DB
from .ebay import EbayClient
from .kinokuniya import KinokuniyaClient
from .models import EbayComps, KinoProduct, ScanRequest
from .pricing import evaluate

log = logging.getLogger("arbitrage.scanner")


def normalize_isbns(raw: list[str]) -> list[str]:
    out: list[str] = []
    for chunk in raw:
        for tok in re.split(r"[\s,;]+", chunk or ""):
            tok = tok.replace("-", "").strip()
            if re.fullmatch(r"97[89]\d{10}|\d{9}[\dXx]", tok):
                out.append(tok.upper())
    # de-dupe, keep order
    seen: set[str] = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def describe(req: ScanRequest) -> str:
    if req.isbns:
        n = len(normalize_isbns(req.isbns))
        return f"{n} ISBN{'s' if n != 1 else ''}"
    if req.url:
        return req.url
    return f'"{req.keyword}"'


async def fetch_products(req: ScanRequest, s: Settings) -> list[KinoProduct]:
    max_items = req.max_items or s.max_items_per_scan
    if s.mock_mode:
        if req.isbns:
            return mock.products(normalize_isbns(req.isbns))[:max_items]
        return mock.search(req.keyword or req.url or "", max_items)
    async with KinokuniyaClient(s) as kino:
        if req.isbns:
            return await kino.products(normalize_isbns(req.isbns)[:max_items])
        if req.url:
            return await kino.listing(req.url, max_items)
        return await kino.search(req.keyword or "", max_items)


async def fetch_comps(product: KinoProduct, db: DB, ebay: EbayClient | None, s: Settings) -> EbayComps:
    cached = db.cached_comps(product.isbn)
    if cached:
        return EbayComps.model_validate(cached)
    if s.mock_mode or ebay is None:
        comps = mock.comps(product.isbn, product.price)
    else:
        comps = await ebay.comps(product.isbn, product.title)
    if not comps.error:
        db.cache_comps(product.isbn, comps.model_dump())
    return comps


async def run_scan(scan_id: int, req: ScanRequest, db: DB, s: Settings = default_settings) -> None:
    db.update_scan(scan_id, status="running", message="Fetching products from Kinokuniya…")
    try:
        products = await fetch_products(req, s)
    except Exception as e:  # noqa: BLE001
        log.exception("kinokuniya fetch failed")
        db.update_scan(scan_id, status="error", message=f"Kinokuniya fetch failed: {e}")
        return

    if not products:
        db.update_scan(scan_id, status="done", total=0, done=0, message="No products found.")
        return

    db.update_scan(scan_id, total=len(products), message=f"Looking up eBay comps for {len(products)} items…")
    ebay: EbayClient | None = None
    if not s.mock_mode:
        ebay = EbayClient(s)
        await ebay.__aenter__()
    try:
        for i, p in enumerate(products, 1):
            try:
                comps = await fetch_comps(p, db, ebay, s)
            except Exception as e:  # noqa: BLE001
                log.exception("ebay comps failed for %s", p.isbn)
                comps = EbayComps(isbn=p.isbn, error=str(e))
            opp = evaluate(p, comps, req.member, s)
            db.add_result(scan_id, p.isbn, opp.model_dump())
            db.update_scan(scan_id, done=i)
            if not s.mock_mode:
                await asyncio.sleep(s.request_delay)
    finally:
        if ebay is not None:
            await ebay.__aexit__(None, None, None)
    db.update_scan(scan_id, status="done", message="Complete.")
