import json
from pathlib import Path

from arbitrage.ebay import parse_browse_response, parse_sold_html
from arbitrage.kinokuniya import handles_from_html, product_from_js, product_from_listing_json, with_page

FX = Path(__file__).parent / "fixtures"
BASE = "https://usa.kinokuniya.com"


def test_product_from_js():
    p = product_from_js(json.loads((FX / "shopify_product.json").read_text()), BASE)
    assert p.isbn == "9781974709939"
    assert p.handle == "chainsaw-man-vol-1-9781974709939"
    assert p.title == "Chainsaw Man, Vol. 1"
    assert p.price == 11.99 and p.sale_price is None
    assert p.author == "VIZ Media"
    assert p.image == "https://usa.kinokuniya.com/cdn/shop/files/csm1.jpg"
    assert p.url == f"{BASE}/products/chainsaw-man-vol-1-9781974709939"
    assert p.in_stock and p.key == "9781974709939"


def test_product_from_js_sale_and_isbn_from_sku():
    p = product_from_js(json.loads((FX / "shopify_product_sale.json").read_text()), BASE)
    assert p.price == 49.99 and p.sale_price == 39.99
    assert p.isbn == "9781506711980"  # pulled from the hyphenated SKU
    assert not p.in_stock
    assert p.image == "https://cdn/x.jpg"


def test_product_without_isbn_uses_handle_key():
    p = product_from_js({"handle": "smiski-figure", "title": "Smiski", "price": 1200,
                         "variants": [{"price": 1200, "available": True, "barcode": "4542202662175"}]}, BASE)
    assert p.isbn == "" and p.key == "handle:smiski-figure" and p.price == 12.0


def test_product_from_listing_json():
    p = product_from_listing_json({"handle": "h", "title": "T", "vendor": "V",
                                   "variants": [{"price": "9.99", "compare_at_price": "12.99", "available": True}],
                                   "images": [{"src": "https://cdn/i.jpg"}]}, BASE)
    assert p.price == 12.99 and p.sale_price == 9.99 and p.image == "https://cdn/i.jpg"


def test_handles_from_html_main_content_only_and_deduped():
    handles = handles_from_html((FX / "shopify_search.html").read_text())
    assert handles == ["chainsaw-man-vol-1-9781974709939", "chainsaw-man-vol-2"]


def test_with_page():
    assert with_page(f"{BASE}/search?q=x", 3) == f"{BASE}/search?q=x&page=3"
    assert with_page(f"{BASE}/search?q=x&page=1", 2) == f"{BASE}/search?q=x&page=2"


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
