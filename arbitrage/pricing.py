"""Fee and profit math. Everything is a plain function so it's easy to test."""
from __future__ import annotations

from statistics import median
from typing import Optional

from .config import Settings, settings as default_settings
from .models import EbayComps, KinoProduct, Opportunity


def cost_basis(product: KinoProduct, member: bool, s: Settings = default_settings) -> float:
    """All-in cost to acquire one copy from Kinokuniya."""
    price = product.effective_price
    # Member discount usually doesn't stack on already-marked-down items.
    if member and product.sale_price is None:
        price = price * (1 - s.kino_member_discount)
    price = price * (1 + s.kino_sales_tax)
    price += s.kino_shipping_per_item
    return round(price, 2)


def ebay_fees(sale_price: float, s: Settings = default_settings) -> float:
    """eBay final value fee. FVF is charged on item price + shipping charged + buyer sales tax."""
    taxed_total = sale_price * (1 + s.buyer_tax_rate)
    return round(taxed_total * s.ebay_fvf_rate + s.ebay_per_order_fee, 2)


def net_profit(sale_price: float, cost: float, s: Settings = default_settings) -> float:
    return round(sale_price - ebay_fees(sale_price, s) - s.ebay_ship_cost - cost, 2)


def pick_target(comps: EbayComps) -> tuple[Optional[float], str]:
    """Choose the price we assume you can sell at.

    Sold comps are the ground truth; if we have them use the median. Otherwise
    undercut the cheapest active listing by a dollar so you'd be the low price.
    """
    if comps.sold_median is not None and comps.sold_count >= 2:
        return comps.sold_median, "sold_median"
    if comps.active_min is not None:
        return round(max(comps.active_min - 1.0, 0.99), 2), "active_min"
    if comps.sold_median is not None:
        return comps.sold_median, "sold_median"
    return None, "none"


def summarize_comps(comps: EbayComps) -> EbayComps:
    active_prices = sorted(l.price + l.shipping for l in comps.active if l.price > 0)
    sold_prices = sorted(l.price + l.shipping for l in comps.sold if l.price > 0)
    comps.active_count = len(active_prices)
    comps.sold_count = len(sold_prices)
    comps.active_min = round(active_prices[0], 2) if active_prices else None
    comps.active_median = round(median(active_prices), 2) if active_prices else None
    comps.sold_median = round(median(sold_prices), 2) if sold_prices else None
    return comps


def verdict(net: Optional[float], roi: Optional[float], comps: EbayComps) -> str:
    if net is None:
        return "unknown"
    if net >= 5 and roi is not None and roi >= 25:
        return "buy"
    if net > 0:
        return "maybe"
    return "pass"


def evaluate(product: KinoProduct, comps: EbayComps, member: bool, s: Settings = default_settings) -> Opportunity:
    comps = summarize_comps(comps)
    cost = cost_basis(product, member, s)
    target, source = pick_target(comps)
    if target is None:
        return Opportunity(
            product=product, comps=comps, cost_basis=cost, target_price=None, target_source=source,
            ebay_fees=None, net_profit=None, margin_pct=None, roi_pct=None, verdict="unknown",
        )
    fees = ebay_fees(target, s)
    net = net_profit(target, cost, s)
    margin = round(net / target * 100, 1) if target else None
    roi = round(net / cost * 100, 1) if cost else None
    return Opportunity(
        product=product, comps=comps, cost_basis=cost, target_price=target, target_source=source,
        ebay_fees=fees, net_profit=net, margin_pct=margin, roi_pct=roi, verdict=verdict(net, roi, comps),
    )
