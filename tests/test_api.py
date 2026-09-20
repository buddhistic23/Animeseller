
import pytest
from fastapi.testclient import TestClient

from arbitrage import main as m
from arbitrage.config import Settings
from arbitrage.db import DB


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "db", DB(str(tmp_path / "t.db")))
    monkeypatch.setattr(m, "settings", Settings(mock_mode=True, request_delay=0))
    with TestClient(m.app) as c:
        yield c


def test_scan_lifecycle_mock(client):
    r = client.post("/api/scans", json={"keyword": "chainsaw man", "max_items": 5})
    assert r.status_code == 200
    sid = r.json()["id"]
    import time
    for _ in range(100):
        s = client.get(f"/api/scans/{sid}").json()
        if s["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert s["status"] == "done", s
    assert s["total"] == 5 and len(s["results"]) == 5
    o = s["results"][0]
    assert {"product", "comps", "cost_basis", "verdict"} <= set(o)

    csv = client.get(f"/api/scans/{sid}/export.csv").text
    assert csv.splitlines()[0].startswith("verdict,isbn,title")
    assert len(csv.splitlines()) == 6


def test_scan_validation(client):
    assert client.post("/api/scans", json={}).status_code == 400
    assert client.post("/api/scans", json={"isbns": ["nope"]}).status_code == 400
    assert client.post("/api/scans", json={"url": "https://evil.example/x"}).status_code == 400


def test_watchlist(client):
    client.post("/api/watchlist", json={"isbn": "9781974709939", "title": "CSM 1"})
    assert [w["isbn"] for w in client.get("/api/watchlist").json()] == ["9781974709939"]
    client.delete("/api/watchlist/9781974709939")
    assert client.get("/api/watchlist").json() == []
