"""Page fetching for Kinokuniya and eBay.

Both sites sit behind bot protection that rejects plain HTTP clients with a
403 even when the User-Agent looks like a browser, because they fingerprint
the TLS handshake. So we try, in order:

  1. curl_cffi impersonating Chrome's TLS/HTTP2 fingerprint (fast; enough for Kinokuniya)
  2. A real headless browser via Playwright, if installed (pip install playwright).
     It uses the Chrome or Edge already on the machine, or Playwright's own
     Chromium after `playwright install chromium`.

FETCHER=auto|curl|browser picks the strategy; auto tries 1 then 2.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional
from urllib.parse import urlparse

from .config import Settings, settings as default_settings

log = logging.getLogger("arbitrage.fetch")

BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


class BlockedError(RuntimeError):
    """The site refused to serve us (403/429/challenge page)."""


def looks_like_challenge(html: str) -> bool:
    h = html[:4000].lower()
    return any(x in h for x in (
        "just a moment", "cf-chl", "challenge-platform", "access denied", "attention required",
        "pardon our interruption", "verify you are a human", "px-captcha", "_incapsula_",
    ))


class Fetcher:
    def __init__(self, s: Settings = default_settings):
        self.s = s
        self.mode = (os.getenv("FETCHER") or os.getenv("KINO_FETCHER") or "auto").strip().lower()
        self._curl = None
        self._pw = None
        self._browser = None
        self._ctx = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        if self._curl is not None:
            await self._curl.close()
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()

    # ---- strategy 1: curl_cffi ----
    async def _fetch_curl(self, url: str) -> str:
        from curl_cffi.requests import AsyncSession  # lazy import so the app runs without it

        if self._curl is None:
            self._curl = AsyncSession(impersonate="chrome", timeout=30, allow_redirects=True)
        r = await self._curl.get(url, headers=BROWSER_HEADERS)
        if r.status_code in (403, 429, 503) or looks_like_challenge(r.text):
            raise BlockedError(f"HTTP {r.status_code}")
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code} for {url}")
        return r.text

    # ---- strategy 2: real browser ----
    async def _fetch_browser(self, url: str) -> str:
        from playwright.async_api import async_playwright  # lazy import

        if self._browser is None:
            self._pw = await async_playwright().start()
            self._browser = await self._launch()
            self._ctx = await self._browser.new_context(
                user_agent=self.s.user_agent, locale="en-US", viewport={"width": 1366, "height": 900},
            )
            # Hide the most obvious automation tell.
            await self._ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
        page = await self._ctx.new_page()
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            # Give a JS challenge a few seconds to resolve itself.
            for _ in range(10):
                html = await page.content()
                if not looks_like_challenge(html):
                    break
                await asyncio.sleep(1)
            html = await page.content()
            status = resp.status if resp else 0
            if status in (403, 429) and looks_like_challenge(html):
                raise BlockedError(f"HTTP {status} (browser)")
            return html
        finally:
            await page.close()

    async def _launch(self):
        """Prefer the user's installed Chrome/Edge (no extra download), then Playwright's Chromium."""
        args = ["--disable-blink-features=AutomationControlled"]
        exe = os.getenv("CHROMIUM_PATH") or None
        attempts = []
        if exe:
            attempts.append({"executable_path": exe})
        attempts += [{"channel": "chrome"}, {"channel": "msedge"}, {}]
        last: Optional[Exception] = None
        for opts in attempts:
            try:
                return await self._pw.chromium.launch(headless=True, args=args, **opts)
            except Exception as e:  # noqa: BLE001
                last = e
        raise ImportError(
            "no browser found. Install Google Chrome, or run: playwright install chromium"
        ) from last

    async def get(self, url: str) -> str:
        errors: list[str] = []
        host = urlparse(url).netloc
        if self.mode in ("auto", "curl"):
            try:
                return await self._fetch_curl(url)
            except ImportError:
                errors.append("curl_cffi not installed (pip install curl_cffi)")
            except BlockedError as e:
                errors.append(f"blocked via curl_cffi: {e}")
                if self.mode == "curl":
                    raise BlockedError(f"{host} refused the request. " + "; ".join(errors)) from e
        if self.mode in ("auto", "browser"):
            try:
                return await self._fetch_browser(url)
            except ImportError as e:
                if "playwright" in str(e).lower() and "no browser" not in str(e):
                    errors.append("headless browser not installed. Run: pip install playwright")
                else:
                    errors.append(str(e))
            except BlockedError as e:
                errors.append(f"blocked via browser: {e}")
        raise BlockedError(f"{host} refused the request. " + "; ".join(errors))
