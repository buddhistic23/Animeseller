from __future__ import annotations

import asyncio
import csv
import io
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import settings
from .db import DB
from .models import ScanRequest
from .fetch import Fetcher
from .kinokuniya import handles_from_html
from .scanner import describe, normalize_isbns, run_scan

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

STATIC = Path(__file__).resolve().parent.parent / "static"
app = FastAPI(title="Kinokuniya → eBay Arbitrage")
db = DB()
_tasks: set[asyncio.Task] = set()


class WatchRequest(BaseModel):
    isbn: str
    title: str = ""
    note: str = ""


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/settings")
async def get_settings():
    return {
        "mock_mode": settings.mock_mode,
        "ebay_configured": settings.ebay_configured,
        "ebay_sold_scrape": settings.ebay_sold_scrape,
        "ebay_scrape": settings.ebay_scrape,
        "kino_base_url": settings.kino_base_url,
        "kino_member_discount": settings.kino_member_discount,
        "kino_sales_tax": settings.kino_sales_tax,
        "kino_shipping_per_item": settings.kino_shipping_per_item,
        "ebay_fvf_rate": settings.ebay_fvf_rate,
        "ebay_per_order_fee": settings.ebay_per_order_fee,
        "ebay_ship_cost": settings.ebay_ship_cost,
        "buyer_tax_rate": settings.buyer_tax_rate,
        "max_items_per_scan": settings.max_items_per_scan,
    }


@app.post("/api/scans")
async def create_scan(req: ScanRequest):
    if req.isbns:
        if not normalize_isbns(req.isbns):
            raise HTTPException(400, "No valid ISBNs found.")
    elif req.url:
        if "kinokuniya.com" not in req.url:
            raise HTTPException(400, "URL must be a usa.kinokuniya.com page")
    elif not (req.keyword and req.keyword.strip()):
        raise HTTPException(400, "Provide a keyword, a Kinokuniya URL, or a list of ISBNs.")
    if req.max_items is not None:
        req.max_items = max(1, min(req.max_items, 500))
    scan_id = db.create_scan(describe(req), req.model_dump())
    task = asyncio.create_task(run_scan(scan_id, req, db, settings))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"id": scan_id}


@app.get("/api/scans")
async def list_scans():
    return db.list_scans()


@app.get("/api/scans/{scan_id}")
async def get_scan(scan_id: int):
    scan = db.get_scan(scan_id)
    if not scan:
        raise HTTPException(404, "scan not found")
    scan["results"] = db.results(scan_id)
    return scan


@app.delete("/api/scans/{scan_id}")
async def delete_scan(scan_id: int):
    db.delete_scan(scan_id)
    return {"ok": True}


@app.get("/api/scans/{scan_id}/export.csv")
async def export_csv(scan_id: int):
    rows = db.results(scan_id)
    if not db.get_scan(scan_id):
        raise HTTPException(404, "scan not found")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "verdict", "isbn", "title", "kino_price", "kino_sale_price", "cost_basis", "target_price",
        "target_source", "ebay_fees", "net_profit", "roi_pct", "margin_pct", "active_min",
        "active_median", "active_count", "sold_median", "sold_count", "kino_url", "in_stock",
    ])
    for o in rows:
        p, c = o["product"], o["comps"]
        w.writerow([
            o["verdict"], p["isbn"], p["title"], p["price"], p.get("sale_price"), o["cost_basis"],
            o.get("target_price"), o["target_source"], o.get("ebay_fees"), o.get("net_profit"),
            o.get("roi_pct"), o.get("margin_pct"), c.get("active_min"), c.get("active_median"),
            c.get("active_count"), c.get("sold_median"), c.get("sold_count"), p["url"], p["in_stock"],
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="scan-{scan_id}.csv"'},
    )


@app.get("/api/watchlist")
async def get_watchlist():
    return db.watchlist()


@app.post("/api/watchlist")
async def add_watch(req: WatchRequest):
    db.watch(req.isbn, req.title, req.note)
    return {"ok": True}


@app.delete("/api/watchlist/{isbn}")
async def remove_watch(isbn: str):
    db.unwatch(isbn)
    return {"ok": True}


@app.get("/api/debug/page")
async def debug_page(url: str, parse: bool = False):
    """Return the raw HTML the scraper sees for a Kinokuniya URL (for fixing the parser),
    or with ?parse=1 a summary of what the parser extracted from it."""
    if "kinokuniya.com" not in url:
        raise HTTPException(400, "URL must be a usa.kinokuniya.com page")
    async with Fetcher(settings) as f:
        html = await f.get(url)
    if parse:
        return {"url": url, "bytes": len(html), "handles": handles_from_html(html)}
    return PlainTextResponse(
        html, media_type="text/html",
        headers={"Content-Disposition": 'attachment; filename="kinokuniya-page.html"'},
    )


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
