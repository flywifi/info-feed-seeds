import unittest

from feeder.adapters import feed, get_adapter
from tests.helpers import read_fixture


class AtomTests(unittest.TestCase):
    def setUp(self):
        self.parsed = feed.parse_feed(read_fixture("feeds", "atom_namespaced.xml"))

    def test_feed_level(self):
        self.assertEqual(self.parsed["kind"], "atom")
        self.assertEqual(self.parsed["title"], "Example channel feed (fixture)")
        self.assertEqual(self.parsed["link"], "https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw")
        self.assertEqual(len(self.parsed["items"]), 2)

    def test_entry_prefers_rel_alternate_and_reads_namespaces(self):
        one = self.parsed["items"][0]
        self.assertEqual(one.link, "https://www.youtube.com/watch?v=fixture0001")
        self.assertEqual(one.guid, "yt:video:fixture0001")
        self.assertEqual(one.title, "Fixture entry one & the ampersand")
        self.assertEqual(one.published, "2026-10-15T14:30:00Z")
        self.assertEqual(one.authors, ["Example channel"])
        self.assertEqual(one.extra["yt_video_id"], "fixture0001")
        self.assertEqual(one.extra["yt_channel_id"], "UC_x5XG1OV2P6uZZ5FSM9Ttw")
        self.assertEqual(one.extra["media_thumbnail"], "https://i.ytimg.com/vi/fixture0001/hqdefault.jpg")
        self.assertEqual(one.extra["media_content"][0]["url"], "https://www.youtube.com/v/fixture0001?version=3")
        self.assertEqual(one.summary, "A description that lives only in the media namespace.")

    def test_entry_falls_back_to_updated_and_bare_link(self):
        two = self.parsed["items"][1]
        self.assertEqual(two.link, "https://www.youtube.com/watch?v=fixture0002")
        self.assertEqual(two.published, "2026-10-10T12:00:00Z")
        self.assertEqual(two.summary, "Entry two has a summary element and no published element.")
        self.assertEqual(two.authors, [])


class RssTests(unittest.TestCase):
    def setUp(self):
        self.parsed = feed.parse_feed(read_fixture("feeds", "rss_pubdate.xml"))

    def test_pubdate_enclosure_itunes_media(self):
        self.assertEqual(self.parsed["kind"], "rss")
        ep = self.parsed["items"][0]
        self.assertEqual(ep.published, "2026-10-14T13:30:00Z")
        self.assertEqual(ep.guid, "ep-0012")
        self.assertEqual(ep.link, "https://example.invalid/episodes/12?utm_source=rss&utm_medium=feed")
        self.assertIn("<b>markup</b>", ep.summary, "raw summary keeps the feed's markup")
        self.assertEqual(ep.extra["content_encoded"], "<p>The full show notes.</p>")
        self.assertEqual(ep.extra["enclosures"][0]["url"], "https://example.invalid/media/12.mp3")
        self.assertEqual(ep.extra["itunes_duration"], "00:42:10")
        self.assertEqual(ep.extra["itunes_episode"], "12")
        self.assertEqual(ep.extra["itunes_image"], "https://example.invalid/art/12.jpg")
        self.assertEqual(ep.extra["media_thumbnail"], "https://example.invalid/art/12-thumb.jpg")
        self.assertEqual(ep.extra["categories"], ["security", "operations"])
        self.assertEqual(ep.authors, ["Fixture Host", "Fixture Host"])

    def test_permalink_guid_becomes_link(self):
        item = self.parsed["items"][1]
        self.assertEqual(item.link, "https://example.invalid/posts/permalink-guid")
        self.assertEqual(item.guid, "https://example.invalid/posts/permalink-guid")
        self.assertEqual(item.published, "2026-10-13T12:00:00Z")

    def test_unparseable_date_is_null(self):
        self.assertIsNone(self.parsed["items"][2].published)


class RdfTests(unittest.TestCase):
    def test_dc_date_and_rdf_about(self):
        parsed = feed.parse_feed(read_fixture("feeds", "rss_dcdate.xml"))
        self.assertEqual(parsed["kind"], "rdf")
        self.assertEqual(parsed["title"], "Fixture RSS 1.0 feed")
        one, two = parsed["items"]
        self.assertEqual(one.published, "2026-10-12T10:15:00Z")
        self.assertEqual(one.authors, ["Fixture Author"])
        self.assertEqual(one.guid, "https://example.invalid/rdf/one")
        self.assertEqual(two.link, "https://example.invalid/rdf/two", "link falls back to rdf:about")
        self.assertEqual(two.published, "2026-10-11T21:30:00Z")


class JsonFeedTests(unittest.TestCase):
    def test_json_feed(self):
        parsed = feed.parse_feed(read_fixture("feeds", "feed.json"))
        self.assertEqual(parsed["kind"], "jsonfeed")
        self.assertEqual(parsed["title"], "Fixture JSON Feed")
        one, two = parsed["items"]
        self.assertEqual(one.guid, "jf-1")
        self.assertEqual(one.link, "https://example.invalid/jf/1")
        self.assertEqual(one.summary, "A short summary.")
        self.assertEqual(one.published, "2026-10-09T15:00:00Z")
        self.assertEqual(one.authors, ["Item Author"])
        self.assertEqual(one.extra["tags"], ["fixtures", "json"])
        self.assertEqual(two.link, "https://example.invalid/elsewhere/2")
        self.assertEqual(two.summary, "Plain text content.")
        self.assertEqual(two.published, "2026-10-08T20:00:00Z")
        self.assertEqual(two.authors, ["Legacy Author Field"])

    def test_json_that_is_not_a_feed(self):
        with self.assertRaises(feed.FeedParseError):
            feed.parse_feed(b'{"items": []}')
        with self.assertRaises(feed.FeedParseError):
            feed.parse_feed(b'{"version": "https://jsonfeed.org/version/1", "items": "no"}')
        with self.assertRaises(feed.FeedParseError):
            feed.parse_feed(b"{not json")


class RefusalTests(unittest.TestCase):
    def test_malformed_feed_raises(self):
        with self.assertRaises(feed.FeedParseError):
            feed.parse_feed(read_fixture("feeds", "malformed.xml"))

    def test_entity_declaration_is_refused_before_parsing(self):
        with self.assertRaises(feed.FeedRefused):
            feed.parse_feed(read_fixture("feeds", "entity.xml"))
        with self.assertRaises(feed.FeedRefused):
            feed.parse_feed(b'<?xml version="1.0"?><!DOCTYPE x [<!entity a "b">]><rss/>')

    def test_non_feed_xml_and_empty(self):
        with self.assertRaises(feed.FeedParseError):
            feed.parse_feed(b"<html><body>no</body></html>")
        with self.assertRaises(feed.FeedParseError):
            feed.parse_feed(b"")

    def test_detect_kind(self):
        self.assertEqual(feed.detect_kind(read_fixture("feeds", "atom_namespaced.xml")), "atom")
        self.assertEqual(feed.detect_kind(read_fixture("feeds", "rss_pubdate.xml")), "rss")
        self.assertEqual(feed.detect_kind(read_fixture("feeds", "rss_dcdate.xml")), "rdf")
        self.assertEqual(feed.detect_kind(read_fixture("feeds", "feed.json")), "jsonfeed")
        self.assertIsNone(feed.detect_kind(b"<html></html>"))

    def test_adapter_registry(self):
        self.assertIs(get_adapter("feed"), feed.parse_feed)
        with self.assertRaises(KeyError):
            get_adapter("nope")


class EmptyElementTests(unittest.TestCase):
    """The ElementTree falsiness trap: an element with no children is falsy, so `find(a) or find(b)`
    silently picks the wrong branch. The adapter must treat an empty-but-present element correctly."""

    def test_empty_title_element_present_with_no_fallback_taken(self):
        doc = (b'<?xml version="1.0"?><rss version="2.0"><channel><title/>'
               b'<item><title></title><link>https://example.invalid/e</link>'
               b'<description></description></item></channel></rss>')
        parsed = feed.parse_feed(doc)
        self.assertEqual(parsed["title"], None)
        item = parsed["items"][0]
        self.assertEqual(item.title, "")
        self.assertEqual(item.link, "https://example.invalid/e")

    def test_atom_summary_present_but_empty_then_content(self):
        doc = (b'<feed xmlns="http://www.w3.org/2005/Atom"><title>t</title><entry><id>1</id>'
               b'<title>x</title><summary></summary><content>body</content></entry></feed>')
        item = feed.parse_feed(doc)["items"][0]
        self.assertEqual(item.summary, "body")


if __name__ == "__main__":
    unittest.main()
