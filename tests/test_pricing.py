from arbitrage.config import Settings
from arbitrage.models import EbayComps, EbayListing, KinoProduct
from arbitrage.pricing import cost_basis, ebay_fees, evaluate, pick_target, summarize_comps

S = Settings(
    kino_member_discount=0.10, kino_sales_tax=0.0, kino_shipping_per_item=0.0,
    ebay_fvf_rate=0.1325, ebay_per_order_fee=0.30, ebay_ship_cost=5.0, buyer_tax_rate=0.0,
)


def prod(price=10.0, sale=None):
    return KinoProduct(isbn="9781974709939", title="t", url="u", price=price, sale_price=sale)


def test_cost_basis_member_discount_not_on_sale_items():
    assert cost_basis(prod(10.0), member=True, s=S) == 9.0
    assert cost_basis(prod(10.0), member=False, s=S) == 10.0
    assert cost_basis(prod(10.0, sale=7.0), member=True, s=S) == 7.0


def test_cost_basis_tax_and_shipping():
    s2 = Settings(**{**S.__dict__, "kino_sales_tax": 0.10, "kino_shipping_per_item": 1.0})
    assert cost_basis(prod(10.0), member=False, s=s2) == 12.0


def test_ebay_fees():
    assert ebay_fees(100.0, S) == 13.55


def test_summarize_and_target_prefers_sold_median():
    comps = EbayComps(
        isbn="x",
        active=[EbayListing(item_id="1", title="a", price=30, shipping=0, url=""),
                EbayListing(item_id="2", title="b", price=20, shipping=5, url="")],
        sold=[EbayListing(item_id="", title="s", price=p, url="", sold=True) for p in (18, 22, 40)],
    )
    comps = summarize_comps(comps)
    assert comps.active_min == 25.0 and comps.active_median == 27.5
    assert comps.sold_median == 22.0 and comps.sold_count == 3
    assert pick_target(comps) == (22.0, "sold_median")


def test_target_falls_back_to_undercut_active_min():
    comps = summarize_comps(EbayComps(isbn="x", active=[EbayListing(item_id="1", title="a", price=30, url="")]))
    assert pick_target(comps) == (29.0, "active_min")
    assert pick_target(EbayComps(isbn="x")) == (None, "none")
    one_sold = summarize_comps(EbayComps(isbn="x", sold=[EbayListing(item_id="", title="", price=15, url="", sold=True)]))
    assert pick_target(one_sold) == (15.0, "sold_median")


def test_evaluate_verdicts():
    good = EbayComps(isbn="x", sold=[EbayListing(item_id="", title="", price=40, url="", sold=True)] * 3)
    o = evaluate(prod(10.0), good, member=True, s=S)
    # cost 9.00, sell 40, fees 5.60, ship 5 -> net 20.40
    assert o.net_profit == 20.4 and o.verdict == "buy" and o.roi_pct == 226.7

    bad = EbayComps(isbn="x", sold=[EbayListing(item_id="", title="", price=10, url="", sold=True)] * 3)
    o = evaluate(prod(10.0), bad, member=True, s=S)
    assert o.net_profit < 0 and o.verdict == "pass"

    o = evaluate(prod(10.0), EbayComps(isbn="x"), member=True, s=S)
    assert o.verdict == "unknown" and o.target_price is None
