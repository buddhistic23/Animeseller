from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT NOT NULL,
  source TEXT NOT NULL,
  request_json TEXT NOT NULL,
  total INTEGER DEFAULT 0,
  done INTEGER DEFAULT 0,
  message TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS results (
  scan_id INTEGER NOT NULL,
  isbn TEXT NOT NULL,
  opportunity_json TEXT NOT NULL,
  PRIMARY KEY (scan_id, isbn)
);
CREATE TABLE IF NOT EXISTS ebay_cache (
  isbn TEXT PRIMARY KEY,
  comps_json TEXT NOT NULL,
  fetched_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS watchlist (
  isbn TEXT PRIMARY KEY,
  title TEXT,
  note TEXT,
  added_at TEXT NOT NULL
);
"""


class DB:
    def __init__(self, path: Optional[str] = None):
        self.path = path or settings.db_path
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # ---- scans ----
    def create_scan(self, source: str, request: dict) -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO scans(status, source, request_json, created_at) VALUES (?,?,?,?)",
                ("queued", source, json.dumps(request), time.strftime("%Y-%m-%dT%H:%M:%S")),
            )
            return int(cur.lastrowid)

    def update_scan(self, scan_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.conn() as c:
            c.execute(f"UPDATE scans SET {cols} WHERE id=?", (*fields.values(), scan_id))

    def get_scan(self, scan_id: int) -> Optional[dict]:
        with self.conn() as c:
            row = c.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
            return dict(row) if row else None

    def list_scans(self, limit: int = 50) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM scans ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    def delete_scan(self, scan_id: int) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM results WHERE scan_id=?", (scan_id,))
            c.execute("DELETE FROM scans WHERE id=?", (scan_id,))

    # ---- results ----
    def add_result(self, scan_id: int, isbn: str, opportunity: dict) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO results(scan_id, isbn, opportunity_json) VALUES (?,?,?)",
                (scan_id, isbn, json.dumps(opportunity)),
            )

    def results(self, scan_id: int) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("SELECT opportunity_json FROM results WHERE scan_id=?", (scan_id,)).fetchall()
            return [json.loads(r["opportunity_json"]) for r in rows]

    # ---- ebay cache ----
    def cached_comps(self, isbn: str, max_age: float = 6 * 3600) -> Optional[dict]:
        with self.conn() as c:
            row = c.execute("SELECT comps_json, fetched_at FROM ebay_cache WHERE isbn=?", (isbn,)).fetchone()
            if row and time.time() - row["fetched_at"] < max_age:
                return json.loads(row["comps_json"])
            return None

    def cache_comps(self, isbn: str, comps: dict) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO ebay_cache(isbn, comps_json, fetched_at) VALUES (?,?,?)",
                (isbn, json.dumps(comps), time.time()),
            )

    # ---- watchlist ----
    def watch(self, isbn: str, title: str, note: str = "") -> None:
        with self.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO watchlist(isbn, title, note, added_at) VALUES (?,?,?,?)",
                (isbn, title, note, time.strftime("%Y-%m-%dT%H:%M:%S")),
            )

    def unwatch(self, isbn: str) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM watchlist WHERE isbn=?", (isbn,))

    def watchlist(self) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM watchlist ORDER BY added_at DESC").fetchall()]
