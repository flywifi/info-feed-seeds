"""The adapter registry: names match the source schema, configured routes are the ones the poller
reads, the feed adapter speaks the shared shape, and route lint names the missing params."""
import unittest

from feeder import adapters, jsonio
from feeder.adapters import feed
from tests.helpers import ROOT

FEED = b'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title><link>https://example.gov/</link>' \
       b'<description>d</description><item><title>One</title><link>https://example.gov/1</link><guid>1</guid>' \
       b'</item></channel></rss>'


class RegistryTests(unittest.TestCase):
    def test_required_params_name_every_adapter_the_schema_allows(self):
        schema = jsonio.load_json(ROOT / "schema" / "source.schema.json")
        allowed = set(schema["$defs"]["route"]["properties"]["adapter"]["enum"])
        self.assertEqual(set(adapters.REQUIRED_PARAMS), allowed)
        self.assertTrue(set(adapters.ADAPTERS) <= allowed)
        self.assertIn("feed", adapters.ADAPTERS)

    def test_configured_routes(self):
        self.assertTrue(adapters.configured({"adapter": "feed", "url": "u", "params": {}}))
        self.assertFalse(adapters.configured({"adapter": "page", "url": "u", "params": {}}), "a landing route")
        self.assertFalse(adapters.configured({"adapter": "sitemap", "url": "u", "params": {}}), "no module yet")
        self.assertFalse(adapters.configured({"adapter": "bogus", "url": "u"}))
        with_stub = dict(adapters.ADAPTERS, page=lambda body, url, params: {"kind": "page", "items": [], "follow": []})
        try:
            adapters.ADAPTERS.update(with_stub)
            self.assertTrue(adapters.configured({"adapter": "page", "url": "u", "params": {"item_selector": "li"}}))
            self.assertFalse(adapters.configured({"adapter": "page", "url": "u", "params": {"title_selector": "h2"}}))
        finally:
            adapters.ADAPTERS.pop("page", None)

    def test_feed_parse_returns_the_shared_shape_and_error(self):
        parsed = feed.parse(FEED, url="https://example.gov/f.xml", params={})
        self.assertEqual((parsed["kind"], len(parsed["items"]), parsed["follow"]), ("rss", 1, []))
        self.assertTrue(issubclass(feed.FeedParseError, adapters.AdapterError))
        with self.assertRaises(adapters.AdapterError):
            feed.parse(b"<html><body>bot wall</body></html>", url="https://example.gov/f.xml", params={})

    def test_lint_route_names_missing_params(self):
        self.assertEqual(adapters.lint_route({"adapter": "page", "url": "u", "params": {}}), [])
        self.assertEqual(adapters.lint_route({"adapter": "page", "url": "u", "params": {"title_selector": "h2"}}),
                         ["route u: page selectors need params.item_selector"])
        self.assertEqual(adapters.lint_route({"adapter": "xml", "url": "u", "params": {"fields": {"title": "t"}}}),
                         ["route u: xml route needs params.item_path"])
        self.assertEqual(adapters.lint_route({"adapter": "json-api", "url": "u", "params": {}}), [], "unconfigured, not wrong")
        self.assertEqual(adapters.lint_route({"adapter": "bogus", "url": "u"}), ["route u: unknown adapter 'bogus'"])

    def test_follow_limit(self):
        self.assertEqual(adapters.follow_limit({"params": {}}), adapters.DEFAULT_FOLLOW_LIMIT)
        self.assertEqual(adapters.follow_limit({"params": {"max_sitemaps": 7}}), 7)
        self.assertEqual(adapters.follow_limit({"params": {"max_pages": 2}}), 2)
        self.assertEqual(adapters.follow_limit({"params": {"max_pages": 0}}), adapters.DEFAULT_FOLLOW_LIMIT)


if __name__ == "__main__":
    unittest.main()
