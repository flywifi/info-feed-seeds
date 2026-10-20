import hashlib
import unittest

from feeder import items


class IdentityTests(unittest.TestCase):
    def test_content_sha256_uses_guid_then_link_and_stripped_title(self):
        expected = hashlib.sha256(b"feed-a|guid-1|Title").hexdigest()
        self.assertEqual(items.content_sha256("feed-a", "guid-1", "https://x/", "  Title \n"), expected)
        by_link = hashlib.sha256(b"feed-a|https://x/|Title").hexdigest()
        self.assertEqual(items.content_sha256("feed-a", None, "https://x/", "Title"), by_link)
        self.assertEqual(items.content_sha256("feed-a", "", "https://x/", "Title"), by_link)
        self.assertEqual(items.content_sha256("feed-a", None, None, "Title"),
                         hashlib.sha256(b"feed-a||Title").hexdigest())


class DateTests(unittest.TestCase):
    def test_rfc822_and_iso_become_utc_z(self):
        self.assertEqual(items.to_utc_z("Tue, 14 Oct 2026 09:30:00 -0400"), "2026-10-14T13:30:00Z")
        self.assertEqual(items.to_utc_z("Mon, 13 Oct 2026 12:00:00 GMT"), "2026-10-13T12:00:00Z")
        self.assertEqual(items.to_utc_z("2026-10-11T23:30:00+02:00"), "2026-10-11T21:30:00Z")
        self.assertEqual(items.to_utc_z("2026-10-12T10:15:00Z"), "2026-10-12T10:15:00Z")
        self.assertEqual(items.to_utc_z("2026-10-12"), "2026-10-12T00:00:00Z")

    def test_missing_or_unparseable_dates_stay_null(self):
        self.assertIsNone(items.to_utc_z(None))
        self.assertIsNone(items.to_utc_z(""))
        self.assertIsNone(items.to_utc_z("sometime last week"))
        self.assertIsNone(items.to_utc_z("31/02/2026"))


class SanitizeTests(unittest.TestCase):
    def test_strips_html_and_entities(self):
        self.assertEqual(items.sanitize("<p>Hello <b>world</b> &amp; friends</p>"), "Hello world & friends")
        self.assertEqual(items.sanitize("<script>alert(1)</script>safe"), "safe")

    def test_removes_control_zero_width_and_bidi(self):
        self.assertEqual(items.sanitize("a\u200bb\u202ec\u0007d\ufeffe"), "abcde")

    def test_collapses_whitespace_and_replaces_dashes(self):
        self.assertEqual(items.sanitize("  one\n\n two\t\u2014three \u2013 four "), "one two - three - four")
        self.assertNotIn("\u2014", items.sanitize("x\u2014y"))

    def test_caps_summary_at_300_and_redacts_emails_in_display_only(self):
        raw = items.RawItem(title="Contact admin@example.invalid now", summary="word " * 100,
                            link="https://example.invalid/a?utm_source=x#frag")
        display = items.display_fields(raw)
        self.assertEqual(display["title"], f"Contact {items.EMAIL_REDACTION} now")
        self.assertLessEqual(len(display["summary"]), 300)
        self.assertTrue(display["summary"].endswith("..."))
        self.assertEqual(display["link"], "https://example.invalid/a")
        self.assertIn("admin@example.invalid", raw.title, "the raw field is untouched")
        self.assertEqual(items.sanitize("mail me@example.invalid"), "mail me@example.invalid",
                         "redaction is opt-in, not applied to raw fields")


class LinkTests(unittest.TestCase):
    def test_normalize_link(self):
        self.assertEqual(items.normalize_link("HTTPS://Example.INVALID:443/Path/?b=2&utm_source=rss&a=1#top"),
                         "https://example.invalid/Path/?b=2&a=1")
        self.assertEqual(items.normalize_link("http://example.invalid:8080/x"), "http://example.invalid:8080/x")
        self.assertEqual(items.normalize_link("https://example.invalid"), "https://example.invalid/")
        self.assertEqual(items.normalize_link("  https://example.invalid/a?fbclid=1  "), "https://example.invalid/a")
        self.assertEqual(items.normalize_link("relative/path"), "relative/path")

    def test_link_key_equates_scheme_www_trailing_slash_and_query_order(self):
        key = items.link_key("http://www.Example.invalid/posts/1/?b=2&a=1&utm_campaign=z")
        self.assertEqual(key, "example.invalid/posts/1?a=1&b=2")
        self.assertEqual(items.link_key("https://example.invalid/posts/1?a=1&b=2"), key)
        self.assertIsNone(items.link_key(None))
        self.assertIsNone(items.link_key(""))


class KeywordTests(unittest.TestCase):
    def test_word_boundary_and_case(self):
        res = items.match_keywords("New CVE advisory for Routers", ["cve", "router"], [])
        self.assertEqual(res["matched"], ["cve"], "router does not match routers on a word boundary")
        self.assertTrue(res["accepted"])

    def test_exclude_wins_and_phrases(self):
        res = items.match_keywords("Zero-day in the webinar recording", ["zero-day"], ["webinar recording"])
        self.assertEqual(res["excluded"], ["webinar recording"])
        self.assertFalse(res["accepted"])
        self.assertTrue(items.match_keywords("anything", [], [])["accepted"], "no include list accepts all")
        self.assertFalse(items.match_keywords("anything", ["other"], [])["accepted"])

    def test_tag_topics_adds_parent_topic(self):
        tags = items.tag_topics("NIST SP 800-53 update", {"standards/nist": ["nist", "sp 800"],
                                                          "cybersecurity/vulnerabilities": ["cve"]})
        self.assertEqual(tags, ["standards", "standards/nist"])


class BuildTests(unittest.TestCase):
    def test_build_item_shape(self):
        raw = items.RawItem(guid="g", link="https://example.invalid/x", title="T", summary="S",
                            published="2026-10-01T00:00:00Z", authors=["A"], extra={"k": 1})
        item = items.build_item("feed-a", "https://example.invalid/rss.xml", raw, retrieved_at="2026-10-20T00:00:00Z")
        self.assertEqual(item["content_sha256"], items.content_sha256("feed-a", "g", "https://example.invalid/x", "T"))
        self.assertEqual(item["link_key"], "example.invalid/x")
        self.assertEqual(item["display"], {"title": "T", "summary": "S", "link": "https://example.invalid/x"})
        self.assertEqual(item["retrieved_at"], "2026-10-20T00:00:00Z")
        self.assertEqual(item["extra"], {"k": 1})


if __name__ == "__main__":
    unittest.main()
