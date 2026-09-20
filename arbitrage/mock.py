"""Deterministic fake data so the app can be exercised without network access
or eBay credentials (MOCK_MODE=1)."""
from __future__ import annotations

import hashlib
import random
from typing import Iterable, Optional

from .models import EbayComps, EbayListing, KinoProduct

_TITLES = [
    "Chainsaw Man, Vol. {n}", "Jujutsu Kaisen, Vol. {n}", "One Piece Box Set {n}",
    "Spy x Family, Vol. {n}", "Berserk Deluxe Edition Vol. {n}", "Vagabond (VIZBIG) Vol. {n}",
    "Frieren: Beyond Journey's End, Vol. {n}", "Dandadan, Vol. {n}", "Blue Lock, Vol. {n}",
    "Oshi No Ko, Vol. {n}", "Monster Perfect Edition Vol. {n}", "Vinland Saga Deluxe {n}",
]


def _rng(seed: str) -> random.Random:
    return random.Random(int(hashlib.md5(seed.encode()).hexdigest(), 16))


def _isbn13(seed: str) -> str:
    r = _rng("isbn" + seed)
    body = "978" + "".join(str(r.randint(0, 9)) for _ in range(9))
    total = sum((1 if i % 2 == 0 else 3) * int(d) for i, d in enumerate(body))
    return body + str((10 - total % 10) % 10)


def search(keyword: str, max_items: int) -> list[KinoProduct]:
    r = _rng(keyword)
    out = []
    for i in range(min(max_items, 24)):
        t = r.choice(_TITLES).format(n=r.randint(1, 20))
        if keyword and r.random() < 0.6:
            t = f"{keyword.title()} " + t.split(",")[0].split(" Vol")[0] + f", Vol. {r.randint(1,20)}"
        price = round(r.choice([9.99, 11.99, 12.99, 14.99, 19.99, 24.99, 39.99, 49.99, 59.99, 99.99]), 2)
        sale = round(price * 0.7, 2) if r.random() < 0.25 else None
        isbn = _isbn13(f"{keyword}-{i}")
        out.append(KinoProduct(
            isbn=isbn, title=t, url=f"https://united-states.kinokuniya.com/products/{isbn}",
            price=price, sale_price=sale, image=None, author="Mock Author", in_stock=r.random() > 0.1,
        ))
    return out


def products(isbns: Iterable[str]) -> list[KinoProduct]:
    out = []
    for isbn in isbns:
        r = _rng(isbn)
        price = round(r.choice([9.99, 12.99, 14.99, 24.99, 49.99]), 2)
        out.append(KinoProduct(
            isbn=isbn, title=r.choice(_TITLES).format(n=r.randint(1, 20)),
            url=f"https://united-states.kinokuniya.com/products/{isbn}", price=price,
        ))
    return out


def comps(isbn: str, kino_price: Optional[float] = None) -> EbayComps:
    r = _rng("ebay" + isbn)
    base = kino_price or r.choice([12.0, 20.0, 45.0])
    mult = r.choice([0.6, 0.8, 1.0, 1.2, 1.5, 1.9, 2.5])
    center = base * mult
    active = [
        EbayListing(item_id=f"v1|{r.randint(10**11, 10**12)}|0", title=f"Listing {i}",
                    price=round(center * r.uniform(0.9, 1.4), 2), shipping=r.choice([0.0, 0.0, 4.99]),
                    condition="New", url="https://www.ebay.com/itm/0")
        for i in range(r.randint(0, 8))
    ]
    sold = [
        EbayListing(item_id="", title=f"Sold {i}", price=round(center * r.uniform(0.85, 1.15), 2),
                    shipping=0.0, url="https://www.ebay.com/itm/0", sold=True)
        for i in range(r.randint(0, 10))
    ]
    return EbayComps(isbn=isbn, active=active, sold=sold)
