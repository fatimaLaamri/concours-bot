"""SQLite storage: seen announcements, per-site health, and small key/value state."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    site_id     TEXT NOT NULL,
    url_key     TEXT NOT NULL,
    url         TEXT NOT NULL,
    title       TEXT NOT NULL,
    first_seen  REAL NOT NULL,
    notified    INTEGER NOT NULL DEFAULT 0,   -- 0 pending, 1 sent, 2 silent (baseline / flood)
    PRIMARY KEY (site_id, url_key)
);
CREATE INDEX IF NOT EXISTS idx_items_pending ON items(notified, first_seen);

CREATE TABLE IF NOT EXISTS sites (
    site_id         TEXT PRIMARY KEY,
    baselined       INTEGER NOT NULL DEFAULT 0,
    last_ok         REAL,
    last_error      TEXT,
    last_error_at   REAL,
    fail_streak     INTEGER NOT NULL DEFAULT 0,
    rss_url         TEXT,
    insecure_ssl    INTEGER NOT NULL DEFAULT 0,
    last_count      INTEGER
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Storage:
    def __init__(self, path: Path):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        """Flush everything into the single bot.db file (needed before saving it on GitHub)."""
        with self._lock:
            self._db.commit()
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._db.execute("PRAGMA journal_mode=DELETE")
            self._db.close()

    # ---------- key/value ----------
    def get(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO kv(key,value) VALUES(?,?) "
                             "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
            self._db.commit()

    # ---------- sites ----------
    def site_state(self, site_id: str) -> sqlite3.Row:
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO sites(site_id) VALUES(?)", (site_id,))
            self._db.commit()
            return self._db.execute("SELECT * FROM sites WHERE site_id=?", (site_id,)).fetchone()

    def mark_ok(self, site_id: str, count: int, rss_url: str | None, insecure: bool) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE sites SET last_ok=?, fail_streak=0, last_count=?, rss_url=COALESCE(?, rss_url), "
                "insecure_ssl=? WHERE site_id=?",
                (time.time(), count, rss_url, int(insecure), site_id))
            self._db.commit()

    def mark_fail(self, site_id: str, error: str) -> int:
        with self._lock:
            self._db.execute(
                "UPDATE sites SET last_error=?, last_error_at=?, fail_streak=fail_streak+1 WHERE site_id=?",
                (error[:300], time.time(), site_id))
            self._db.commit()
            row = self._db.execute("SELECT fail_streak FROM sites WHERE site_id=?", (site_id,)).fetchone()
        return row["fail_streak"] if row else 0

    def set_baselined(self, site_id: str) -> None:
        with self._lock:
            self._db.execute("UPDATE sites SET baselined=1 WHERE site_id=?", (site_id,))
            self._db.commit()

    def all_site_states(self) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute("SELECT * FROM sites").fetchall()

    # ---------- items ----------
    def known_keys(self, site_id: str) -> set[str]:
        with self._lock:
            rows = self._db.execute("SELECT url_key FROM items WHERE site_id=?", (site_id,)).fetchall()
        return {r["url_key"] for r in rows}

    def add_items(self, site_id: str, items: list[tuple[str, str, str]], notified: int) -> None:
        """items: (url_key, url, title)"""
        now = time.time()
        with self._lock:
            self._db.executemany(
                "INSERT OR IGNORE INTO items(site_id,url_key,url,title,first_seen,notified) VALUES(?,?,?,?,?,?)",
                [(site_id, k, u, t, now, notified) for k, u, t in items])
            self._db.commit()

    def pending(self, limit: int = 100) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT * FROM items WHERE notified=0 ORDER BY first_seen LIMIT ?", (limit,)).fetchall()

    def mark_sent(self, site_id: str, url_key: str) -> None:
        with self._lock:
            self._db.execute("UPDATE items SET notified=1 WHERE site_id=? AND url_key=?", (site_id, url_key))
            self._db.commit()

    def sent_elsewhere(self, url_key: str, site_id: str, days: int = 14) -> bool:
        """Same link already announced from another site recently (e.g. a PDF linked by two sites)."""
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM items WHERE url_key=? AND site_id<>? AND notified=1 AND first_seen>? LIMIT 1",
                (url_key, site_id, time.time() - days * 86400)).fetchone()
        return row is not None

    def mark_silent(self, site_id: str, url_key: str) -> None:
        with self._lock:
            self._db.execute("UPDATE items SET notified=2 WHERE site_id=? AND url_key=?", (site_id, url_key))
            self._db.commit()

    def all_items(self) -> list[sqlite3.Row]:
        """Every saved announcement, newest first (used by /search)."""
        with self._lock:
            return self._db.execute(
                "SELECT site_id, url, title, first_seen, notified FROM items ORDER BY first_seen DESC, rowid ASC"
            ).fetchall()

    def latest_for_site(self, site_id: str, limit: int = 10) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT site_id, url, title, first_seen, notified FROM items WHERE site_id=? "
                "ORDER BY first_seen DESC, rowid ASC LIMIT ?", (site_id, limit)).fetchall()

    def recent_sent(self, limit: int = 10) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT * FROM items WHERE notified=1 ORDER BY first_seen DESC LIMIT ?", (limit,)).fetchall()

    def stats(self) -> dict:
        with self._lock:
            total = self._db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
            sent = self._db.execute("SELECT COUNT(*) c FROM items WHERE notified=1").fetchone()["c"]
            day = self._db.execute("SELECT COUNT(*) c FROM items WHERE notified=1 AND first_seen>?",
                                   (time.time() - 86400,)).fetchone()["c"]
            week = self._db.execute("SELECT COUNT(*) c FROM items WHERE notified=1 AND first_seen>?",
                                    (time.time() - 7 * 86400,)).fetchone()["c"]
        return {"tracked_links": total, "sent_total": sent, "sent_24h": day, "sent_7d": week}
