"""End-to-end KinokuniyaClient against a fake Shopify store served locally."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from arbitrage.config import Settings
from arbitrage.fetch import Fetcher
from arbitrage.kinokuniya import KinokuniyaClient

FX = Path(__file__).parent / "fixtures"
PRODUCTS = {
    "chainsaw-man-vol-1-9781974709939": json.loads((FX / "shopify_product.json").read_text()),
    "chainsaw-man-vol-2": {"handle": "chainsaw-man-vol-2", "title": "Chainsaw Man, Vol. 2", "price": 1199,
                           "variants": [{"price": 1199, "available": True, "barcode": "9781974709946"}]},
    "berserk-deluxe-1": json.loads((FX / "shopify_product_sale.json").read_text()),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def _send(self, body: str, ctype="text/html", status=200):
        b = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/search":
            page = int(q.get("page", ["1"])[0])
            if page > 1:
                return self._send("<main></main>")
            return self._send((FX / "shopify_search.html").read_text())
        if u.path == "/search/suggest.json":
            isbn = q["q"][0]
            hits = [{"handle": h} for h, d in PRODUCTS.items()
                    if any(v.get("barcode") == isbn for v in d["variants"])]
            return self._send(json.dumps({"resources": {"results": {"products": hits}}}), "application/json")
        if u.path.startswith("/collections/manga/products.json"):
            page = int(q.get("page", ["1"])[0])
            items = [] if page > 1 else [
                {"handle": "berserk-deluxe-1", "title": "x", "variants": [{"price": "39.99", "available": False}]}]
            return self._send(json.dumps({"products": items}), "application/json")
        if u.path.startswith("/products/") and u.path.endswith(".js"):
            h = u.path[len("/products/"):-3]
            if h in PRODUCTS:
                return self._send(json.dumps(PRODUCTS[h]), "application/javascript")
            return self._send("not found", status=404)
        return self._send("nope", status=404)


@pytest.fixture(scope="module")
def store():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setenv("FETCHER", "curl")
    s = Settings(kino_base_url=store, request_delay=0)
    return KinokuniyaClient(s)


@pytest.mark.asyncio
async def test_search_resolves_handles_to_products(client):
    async with client as c:
        products = await c.search("chainsaw man", max_items=10)
    assert [p.title for p in products] == ["Chainsaw Man, Vol. 1", "Chainsaw Man, Vol. 2"]
    assert products[0].price == 11.99 and products[0].isbn == "9781974709939"


@pytest.mark.asyncio
async def test_search_respects_max_items(client):
    async with client as c:
        products = await c.search("chainsaw man", max_items=1)
    assert len(products) == 1


@pytest.mark.asyncio
async def test_listing_collection_uses_products_json(client):
    async with client as c:
        products = await c.listing(f"{c.s.kino_base_url}/collections/manga", max_items=10)
    assert [p.handle for p in products] == ["berserk-deluxe-1"]
    assert products[0].sale_price == 39.99


@pytest.mark.asyncio
async def test_listing_single_product_url(client):
    async with client as c:
        products = await c.listing(f"{c.s.kino_base_url}/products/chainsaw-man-vol-2?_pos=1", max_items=10)
    assert len(products) == 1 and products[0].isbn == "9781974709946"


@pytest.mark.asyncio
async def test_product_by_isbn_via_suggest(client):
    async with client as c:
        p = await c.product("9781974709946")
        missing = await c.product("9780000000000")
    assert p is not None and p.handle == "chainsaw-man-vol-2"
    assert missing is None
