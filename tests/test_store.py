"""The SQLite store's schema version: a fresh file, an older file migrated in place, the dry-run copy,
the filter columns, and a newer file refused."""
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from feeder import store as store_mod
from feeder.store import DB_NAME, NEW_COLUMNS, SCHEMA_VERSION, Store, StoreError

# The items, hits and health tables as schema version 1 created them, before any filter column.
V1_SCHEMA = """
CREATE TABLE items (
    content_sha256 TEXT PRIMARY KEY, feed_id TEXT NOT NULL, guid TEXT, title TEXT NOT NULL, link TEXT,
    link_key TEXT, summary TEXT NOT NULL, published TEXT, first_seen TEXT NOT NULL, guard_level TEXT NOT NULL,
    guard_score INTEGER NOT NULL DEFAULT 0, authors TEXT NOT NULL DEFAULT '[]', route_url TEXT, retrieved_at TEXT);
CREATE INDEX items_feed ON items(feed_id);
CREATE INDEX items_link_key ON items(link_key);
CREATE TABLE hits (profile TEXT NOT NULL, content_sha256 TEXT NOT NULL, day TEXT NOT NULL, topics TEXT NOT NULL,
    PRIMARY KEY (profile, content_sha256));
CREATE INDEX hits_day ON hits(profile, day);
CREATE TABLE health (feed_id TEXT NOT NULL, day TEXT NOT NULL, status TEXT NOT NULL, http_status INTEGER, items INTEGER,
    error TEXT, route_url TEXT, source_label TEXT, checked_at TEXT NOT NULL, PRIMARY KEY (feed_id, day));
INSERT INTO items VALUES ('a1', 'nist-news', 'g', 'Old item', 'https://example.gov/a', 'example.gov/a', 's',
    '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', 'CLEAN', 0, '["An Author"]', 'https://example.gov/f.xml', '2026-10-01T00:00:00Z');
INSERT INTO hits VALUES ('tr', 'a1', '2026-10-01', '["cybersecurity"]');
INSERT INTO health VALUES ('nist-news', '2026-10-01', 'ok', 200, 1, NULL, 'https://example.gov/f.xml', 'live', '2026-10-01T00:00:00Z');
"""


def item(sha="b1", **extra):
    rec = {"content_sha256": sha, "feed_id": "fortiguard-psirt", "guid": sha, "title": "Advisory", "link": f"https://example.gov/{sha}",
           "link_key": f"example.gov/{sha}", "summary": "s", "published": "2026-10-21T07:00:00Z", "guard_level": "CLEAN",
           "guard_score": 0, "authors": [], "route_url": "https://example.gov/ir.xml", "retrieved_at": "2026-10-25T00:00:00Z"}
    rec.update(extra)
    return rec


class StoreSchemaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def columns(self, conn):
        return [r[1] for r in conn.execute("PRAGMA table_info(items)")]

    def legacy(self):
        path = self.tmp / DB_NAME
        conn = sqlite3.connect(str(path))
        conn.executescript(V1_SCHEMA)
        conn.commit()
        conn.close()
        return path

    def test_a_fresh_database_has_the_version_and_every_column(self):
        s = Store.open(self.tmp)
        self.assertEqual(s.conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        for name, _ in NEW_COLUMNS:
            self.assertIn(name, self.columns(s.conn))
        s.close()

    def test_an_older_file_is_migrated_in_place_and_keeps_its_rows(self):
        self.legacy()
        s = Store.open(self.tmp)
        self.assertEqual(s.added_columns, [name for name, _ in NEW_COLUMNS])
        self.assertEqual(s.conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        old = s.item("a1")
        self.assertEqual((old["title"], old["authors"], old["industries"], old["cves"], old["exploited"],
                          old["source_category"], old["content_category"]),
                         ("Old item", ["An Author"], [], [], None, None, None))
        self.assertEqual([h["content_sha256"] for h in s.hits_for_day("tr", "2026-10-01")], ["a1"])
        self.assertEqual(s.health_for_day("2026-10-01")["nist-news"]["status"], "ok")
        s.close()
        again = Store.open(self.tmp)
        self.assertEqual(again.added_columns, [], "a second open adds nothing")
        again.close()

    def test_the_dry_run_copy_is_migrated_and_the_file_is_untouched(self):
        path = self.legacy()
        before = path.read_bytes()
        s = Store.open(self.tmp, in_memory_copy=True)
        self.assertIn("content_subcategory", self.columns(s.conn))
        s.upsert_item(item(), first_seen="2026-10-25T00:00:00Z")
        s.close()
        self.assertEqual(path.read_bytes(), before)

    def test_filter_columns_are_written_and_read_back(self):
        s = Store.open(self.tmp)
        self.assertTrue(s.upsert_item(item(publisher="Fortinet FortiGuard Labs", source_category="vulnerabilities",
                                           source_subcategory="vendor-advisories", content_category="attacks",
                                           content_subcategory="exploited-now", content_category_basis="item",
                                           content_subcategory_basis="item", industries=["Cybersecurity"],
                                           cves="CVE-2026-109618, cve-2026-109619", severity="critical", severity_rank=4,
                                           exploited=True, vendors=[], deadline="2026-11-14"),
                                      first_seen="2026-10-25T00:00:00Z"))
        got = s.item("b1")
        self.assertEqual((got["source_category"], got["source_subcategory"]), ("vulnerabilities", "vendor-advisories"))
        self.assertEqual((got["content_category"], got["content_subcategory"], got["content_category_basis"]),
                         ("attacks", "exploited-now", "item"))
        self.assertEqual((got["industries"], got["cves"], got["vendors"]), (["cybersecurity"], ["cve-2026-109618", "cve-2026-109619"], []))
        self.assertEqual((got["severity"], got["severity_rank"], got["exploited"], got["deadline"]), ("critical", 4, True, "2026-11-14"))
        raw = s.conn.execute("SELECT cves, exploited FROM items WHERE content_sha256 = 'b1'").fetchone()
        self.assertEqual(tuple(raw), ("cve-2026-109618,cve-2026-109619", 1))
        s.close()

    def test_a_sighting_without_enrichment_keeps_what_was_stored(self):
        s = Store.open(self.tmp)
        s.upsert_item(item(content_category="attacks", cves=["CVE-2026-1"], exploited=True), first_seen="2026-10-25T00:00:00Z")
        self.assertFalse(s.upsert_item(item(title="Advisory, revised"), first_seen="2026-10-26T00:00:00Z"))
        got = s.item("b1")
        self.assertEqual((got["title"], got["content_category"], got["cves"], got["exploited"], got["first_seen"]),
                         ("Advisory, revised", "attacks", ["cve-2026-1"], True, "2026-10-25T00:00:00Z"))
        s.upsert_item(item(exploited=False, cves=[]), first_seen="2026-10-26T00:00:00Z")
        got = s.item("b1")
        self.assertEqual((got["exploited"], got["cves"]), (None, []), "an enrichment that says nothing clears the value")
        s.close()

    def test_a_newer_file_is_refused(self):
        path = self.tmp / DB_NAME
        conn = sqlite3.connect(str(path))
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        conn.commit()
        conn.close()
        with self.assertRaisesRegex(StoreError, f"schema version {SCHEMA_VERSION + 1}"):
            Store.open(self.tmp)
        with self.assertRaises(StoreError):
            Store.open(self.tmp, in_memory_copy=True)

    def test_list_values_are_lowercased_and_empty_parts_dropped(self):
        self.assertEqual(store_mod._column_value("vendors", ["Fortinet", "", " Cisco "]), "fortinet,cisco")
        self.assertEqual(store_mod._column_value("vendors", None), "")
        self.assertIsNone(store_mod._column_value("exploited", 0))


if __name__ == "__main__":
    unittest.main()
