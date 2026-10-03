import pytest

from arbitrage.fetch import BlockedError, Fetcher, looks_like_challenge


def test_looks_like_challenge():
    assert looks_like_challenge("<html><title>Just a moment...</title>")
    assert not looks_like_challenge("<html><title>Kinokuniya</title>")


@pytest.mark.asyncio
async def test_auto_falls_back_to_browser_when_curl_blocked(monkeypatch):
    f = Fetcher()
    f.mode = "auto"
    calls = []

    async def curl(url):
        calls.append("curl")
        raise BlockedError("HTTP 403")

    async def browser(url):
        calls.append("browser")
        return "<html>ok</html>"

    monkeypatch.setattr(f, "_fetch_curl", curl)
    monkeypatch.setattr(f, "_fetch_browser", browser)
    assert await f.get("https://x") == "<html>ok</html>"
    assert calls == ["curl", "browser"]


@pytest.mark.asyncio
async def test_curl_only_mode_raises_when_blocked(monkeypatch):
    f = Fetcher()
    f.mode = "curl"

    async def curl(url):
        raise BlockedError("HTTP 403")

    monkeypatch.setattr(f, "_fetch_curl", curl)
    with pytest.raises(BlockedError):
        await f.get("https://x")


@pytest.mark.asyncio
async def test_missing_browser_gives_install_hint(monkeypatch):
    f = Fetcher()
    f.mode = "browser"

    async def browser(url):
        raise ImportError("no playwright")

    monkeypatch.setattr(f, "_fetch_browser", browser)
    with pytest.raises(BlockedError, match="playwright install chromium"):
        await f.get("https://x")


def test_legacy_kino_domain_is_upgraded(monkeypatch):
    from arbitrage.config import Settings
    monkeypatch.setenv("KINO_BASE_URL", "https://united-states.kinokuniya.com")
    assert Settings().kino_base_url == "https://usa.kinokuniya.com"
    monkeypatch.setenv("KINO_BASE_URL", "https://usa.kinokuniya.com/")
    assert Settings().kino_base_url == "https://usa.kinokuniya.com"
