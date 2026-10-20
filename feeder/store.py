"""The feeder's SQLite state: every item ever seen, which profile showed it on which day, and the
health of every source poll.

Tables
  items   content_sha256 PK, feed_id, guid, title, link, link_key, summary, published, first_seen,
          guard_level, plus authors (JSON list, as the feed gave them), guard_score, route_url and
          retrieved_at. Raw feed text is stored; display forms are derived at render time.
  hits    (profile, content_sha256) PK, day, topics (JSON list). One row per item a profile
          selected; the day is the digest it belongs to. Day D's digest is re-rendered from here.
  health  (feed_id, day) PK, status, http_status, items, error, route_url, source_label,
          checked_at. The last poll of a source on a day wins. Offline runs never write it.

The database lives at `<state-dir>/feeder.db` (default `.cache/feeder.db`, never tracked).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB_NAME = "feeder.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    content_sha256 TEXT PRIMARY KEY,
    feed_id TEXT NOT NULL,
    guid TEXT,
    title TEXT NOT NULL,
    link TEXT,
    link_key TEXT,
    summary TEXT NOT NULL,
    published TEXT,
    first_seen TEXT NOT NULL,
    guard_level TEXT NOT NULL,
    guard_score INTEGER NOT NULL DEFAULT 0,
    authors TEXT NOT NULL DEFAULT '[]',
    route_url TEXT,
    retrieved_at TEXT
);
CREATE INDEX IF NOT EXISTS items_feed ON items(feed_id);
CREATE INDEX IF NOT EXISTS items_link_key ON items(link_key);
CREATE TABLE IF NOT EXISTS hits (
    profile TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    day TEXT NOT NULL,
    topics TEXT NOT NULL,
    PRIMARY KEY (profile, content_sha256)
);
CREATE INDEX IF NOT EXISTS hits_day ON hits(profile, day);
CREATE TABLE IF NOT EXISTS health (
    feed_id TEXT NOT NULL,
    day TEXT NOT NULL,
    status TEXT NOT NULL,
    http_status INTEGER,
    items INTEGER,
    error TEXT,
    route_url TEXT,
    source_label TEXT,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (feed_id, day)
);
"""


class Store:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # -- opening

    @classmethod
    def open(cls, state_dir: str | Path, *, in_memory_copy: bool = False) -> "Store":
        """Open `<state_dir>/feeder.db`. With `in_memory_copy` the file is copied into memory (if it
        exists) and nothing is ever written back: the dry-run mode."""
        path = Path(state_dir) / DB_NAME
        if in_memory_copy:
            mem = sqlite3.connect(":memory:")
            if path.exists():
                disk = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
                try:
                    disk.backup(mem)
                finally:
                    disk.close()
            return cls(mem)
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(sqlite3.connect(str(path)))

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def commit(self) -> None:
        self.conn.commit()

    # -- items

    def upsert_item(self, rec: dict, first_seen: str) -> bool:
        """Insert or refresh an item. `first_seen` is kept from the first sighting. True when new."""
        row = self.conn.execute("SELECT first_seen FROM items WHERE content_sha256 = ?",
                                (rec["content_sha256"],)).fetchone()
        values = (rec["feed_id"], rec.get("guid"), rec.get("title") or "", rec.get("link"),
                  rec.get("link_key"), rec.get("summary") or "", rec.get("published"),
                  rec["guard_level"], int(rec.get("guard_score") or 0),
                  json.dumps(list(rec.get("authors") or []), ensure_ascii=False),
                  rec.get("route_url"), rec.get("retrieved_at"))
        if row is None:
            self.conn.execute(
                "INSERT INTO items (feed_id, guid, title, link, link_key, summary, published, guard_level,"
                " guard_score, authors, route_url, retrieved_at, content_sha256, first_seen)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values + (rec["content_sha256"], first_seen))
            return True
        self.conn.execute(
            "UPDATE items SET feed_id = ?, guid = ?, title = ?, link = ?, link_key = ?, summary = ?,"
            " published = ?, guard_level = ?, guard_score = ?, authors = ?, route_url = ?, retrieved_at = ?"
            " WHERE content_sha256 = ?", values + (rec["content_sha256"],))
        return False

    def items_for_feeds(self, feed_ids: list[str]) -> list[dict]:
        if not feed_ids:
            return []
        marks = ",".join("?" for _ in feed_ids)
        rows = self.conn.execute(f"SELECT * FROM items WHERE feed_id IN ({marks})", list(feed_ids)).fetchall()
        return [_item_row(r) for r in rows]

    def item(self, sha: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM items WHERE content_sha256 = ?", (sha,)).fetchone()
        return _item_row(row) if row is not None else None

    # -- hits

    def hit_shas(self, profile: str) -> set[str]:
        rows = self.conn.execute("SELECT content_sha256 FROM hits WHERE profile = ?", (profile,)).fetchall()
        return {r[0] for r in rows}

    def hit_link_keys(self, profile: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT i.link_key FROM hits h JOIN items i ON i.content_sha256 = h.content_sha256"
            " WHERE h.profile = ? AND i.link_key IS NOT NULL", (profile,)).fetchall()
        return {r[0] for r in rows}

    def add_hit(self, profile: str, sha: str, day: str, topics: list[str]) -> None:
        self.conn.execute("INSERT OR IGNORE INTO hits (profile, content_sha256, day, topics) VALUES (?, ?, ?, ?)",
                          (profile, sha, day, json.dumps(sorted(topics))))

    def hits_for_day(self, profile: str, day: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT i.*, h.topics AS hit_topics FROM hits h JOIN items i ON i.content_sha256 = h.content_sha256"
            " WHERE h.profile = ? AND h.day = ?", (profile, day)).fetchall()
        out = []
        for r in rows:
            rec = _item_row(r)
            rec["topics"] = json.loads(r["hit_topics"])
            out.append(rec)
        return out

    def days_with_hits(self, profile: str) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT day FROM hits WHERE profile = ? ORDER BY day", (profile,)).fetchall()
        return [r[0] for r in rows]

    # -- health

    def record_health(self, feed_id: str, day: str, *, status: str, http_status: int | None,
                      items: int | None, error: str | None, route_url: str | None,
                      source_label: str | None, checked_at: str) -> None:
        self.conn.execute(
            "INSERT INTO health (feed_id, day, status, http_status, items, error, route_url, source_label, checked_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(feed_id, day) DO UPDATE SET status = excluded.status, http_status = excluded.http_status,"
            " items = excluded.items, error = excluded.error, route_url = excluded.route_url,"
            " source_label = excluded.source_label, checked_at = excluded.checked_at",
            (feed_id, day, status, http_status, items, error, route_url, source_label, checked_at))

    def health_for_day(self, day: str) -> dict[str, dict]:
        rows = self.conn.execute("SELECT * FROM health WHERE day = ?", (day,)).fetchall()
        return {r["feed_id"]: dict(r) for r in rows}

    def previous_health(self, feed_id: str, day: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM health WHERE feed_id = ? AND day < ? ORDER BY day DESC LIMIT 1",
                                (feed_id, day)).fetchone()
        return dict(row) if row is not None else None


def _item_row(row: sqlite3.Row) -> dict:
    rec = {k: row[k] for k in row.keys() if k != "hit_topics"}
    rec["authors"] = json.loads(rec.get("authors") or "[]")
    return rec


__all__ = ["DB_NAME", "SCHEMA", "Store"]
