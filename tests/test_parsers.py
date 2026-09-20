from pathlib import Path

from arbitrage.ebay import parse_browse_response, parse_sold_html
from arbitrage.kinokuniya import parse_listing, parse_product_page, with_page

FX = Path(__file__).parent / "fixtures"
BASE = "https://united-states.kinokuniya.com"


def test_parse_listing_extracts_products():
    products = {p.isbn: p for p in parse_listing((FX / "kino_listing.html").read_text(), BASE)}
    assert set(products) == {"9781974709939", "9781974733644", "9781506711980"}

    csm = products["9781974709939"]
    assert csm.title == "Chainsaw Man, Vol. 1"
    assert csm.price == 9.99 and csm.sale_price is None
    assert csm.author == "Tatsuki Fujimoto"
    assert csm.image == f"{BASE}/images/csm1.jpg"
    assert csm.url == f"{BASE}/products/9781974709939"
    assert csm.in_stock

    jjk = products["9781974733644"]
    assert jjk.price == 12.99 and jjk.sale_price == 8.99

    berserk = products["9781506711980"]
    assert berserk.price == 49.99
    assert not berserk.in_stock


def test_parse_product_page_prefers_json_ld():
    p = parse_product_page((FX / "kino_product.html").read_text(), f"{BASE}/products/9781421520544")
    assert p is not None
    assert p.isbn == "9781421520544"
    assert p.title == "Vagabond (VIZBIG Edition), Vol. 1"
    assert p.price == 19.99
    assert p.author == "Takehiko Inoue"
    assert p.image == "https://cdn.example/vag.jpg"


def test_with_page():
    assert with_page(f"{BASE}/products?keyword=x", 3) == f"{BASE}/products?keyword=x&page=3"
    assert with_page(f"{BASE}/products?keyword=x&page=1", 2) == f"{BASE}/products?keyword=x&page=2"


def test_parse_browse_response():
    data = {"itemSummaries": [
        {"itemId": "v1|1|0", "title": "A", "price": {"value": "10.50"}, "condition": "New",
         "itemWebUrl": "https://ebay.com/itm/1", "shippingOptions": [{"shippingCost": {"value": "2.00"}}]},
        {"itemId": "v1|2|0", "title": "B", "price": {"value": "8"}, "itemWebUrl": "https://ebay.com/itm/2"},
        {"itemId": "bad", "title": "no price"},
    ]}
    ls = parse_browse_response(data)
    assert [(l.price, l.shipping) for l in ls] == [(10.5, 2.0), (8.0, 0.0)]


def test_parse_sold_html_skips_placeholder():
    ls = parse_sold_html((FX / "ebay_sold.html").read_text())
    assert [(l.item_id, l.price, l.shipping) for l in ls] == [("111", 14.5, 3.99), ("222", 12.0, 0.0)]
    assert all(l.sold for l in ls)
