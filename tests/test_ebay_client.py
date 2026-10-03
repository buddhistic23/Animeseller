import pytest

from arbitrage.config import Settings
from arbitrage.ebay import EbayClient
from arbitrage.models import EbayListing


@pytest.mark.asyncio
async def test_no_keys_scrapes_active_and_sold(monkeypatch):
    s = Settings(ebay_client_id="", ebay_client_secret="", ebay_scrape=True, ebay_sold_scrape=False)
    c = EbayClient(s)
    calls = []

    async def fake(query, sold):
        calls.append((query, sold))
        return [EbayListing(item_id="1", title="t", price=10, url="", sold=sold)]

    monkeypatch.setattr(c, "scrape_listings", fake)
    comps = await c.comps("9781974709939", "Chainsaw Man")
    assert calls == [("9781974709939", False), ("9781974709939", True)]
    assert len(comps.active) == 1 and len(comps.sold) == 1 and comps.error is None


@pytest.mark.asyncio
async def test_with_keys_uses_api_and_skips_sold_by_default(monkeypatch):
    s = Settings(ebay_client_id="id", ebay_client_secret="sec", ebay_scrape=True, ebay_sold_scrape=False)
    c = EbayClient(s)
    scraped = []

    async def api(isbn, title=None):
        return [EbayListing(item_id="1", title="t", price=10, url="")]

    async def fake(query, sold):
        scraped.append(sold)
        return []

    monkeypatch.setattr(c, "active_listings", api)
    monkeypatch.setattr(c, "scrape_listings", fake)
    comps = await c.comps("9781974709939")
    assert len(comps.active) == 1 and scraped == []


def test_search_page_urls():
    c = EbayClient(Settings())
    assert "LH_Sold=1" in c.search_page_url("x", sold=True)
    assert "LH_Sold" not in c.search_page_url("x", sold=False)
    assert "LH_BIN=1" in c.search_page_url("x", sold=False)
