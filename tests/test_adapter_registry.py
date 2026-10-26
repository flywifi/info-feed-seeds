"""The adapter registry: names match the source schema, configured routes are the ones the poller
reads, the feed adapter speaks the shared shape, and route lint names the missing params."""
import sys
import unittest

from feeder import adapters, jsonio
from feeder.adapters import feed
from tests.helpers import ROOT

sys.path.insert(0, str(ROOT / "tools"))
import commit_check as cc  # noqa: E402

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
        self.assertTrue(adapters.configured({"adapter": "sitemap", "url": "u", "params": {}}))
        self.assertFalse(adapters.configured({"adapter": "bogus", "url": "u"}))
        self.assertTrue(adapters.configured({"adapter": "page", "url": "u", "params": {"item_selector": "li"}}))
        self.assertFalse(adapters.configured({"adapter": "page", "url": "u", "params": {"title_selector": "h2"}}))

    def test_the_commit_checker_mirrors_the_registry(self):
        self.assertEqual(set(cc._POLLED_ADAPTERS), set(adapters.ADAPTERS))
        self.assertEqual(cc._ROUTE_PARAMS, {name: keys for name, keys in adapters.REQUIRED_PARAMS.items() if keys})

    def test_check_result_refuses_a_malformed_adapter_return(self):
        good = {"kind": "page", "items": [], "follow": []}
        self.assertIs(adapters.check_result(good), good)
        for bad in (None, {}, {"items": None}, {"items": [{"title": "t"}]}, {"items": [], "follow": "https://example.gov/"}):
            with self.subTest(bad=bad), self.assertRaises(TypeError):
                adapters.check_result(bad)

    def test_feed_parse_returns_the_shared_shape_and_error(self):
        parsed = feed.parse(FEED, url="https://example.gov/f.xml", params={})
        self.assertEqual((parsed["kind"], len(parsed["items"]), parsed["follow"]), ("rss", 1, []))
        self.assertTrue(issubclass(feed.FeedParseError, adapters.AdapterError))
        with self.assertRaises(adapters.AdapterError):
            feed.parse(b"<html><body>bot wall</body></html>", url="https://example.gov/f.xml", params={})

    def test_lint_route_names_missing_params(self):
        self.assertEqual(adapters.lint_route({"adapter": "page", "kind": "page", "url": "u", "params": {}}), [])
        self.assertEqual(adapters.lint_route({"adapter": "page", "kind": "page", "url": "u", "params": {"title_selector": "h2"}}),
                         ["route u: page selectors need params.item_selector"])
        self.assertEqual(adapters.lint_route({"adapter": "xml", "kind": "xml", "url": "u", "params": {"fields": {"title": "t"}}}), [],
                         "the schema refuses a missing item_path; lint does not repeat it")
        self.assertEqual(adapters.lint_route({"adapter": "bogus", "url": "u"}), ["route u: unknown adapter 'bogus'"])

    def test_follow_url_keeps_the_route_host_and_web_schemes_only(self):
        route = "https://example.gov/sitemap.xml"
        self.assertEqual(adapters.follow_url(route, "https://example.gov/a.xml#x"), "https://example.gov/a.xml")
        self.assertEqual(adapters.follow_url(route, "https://EXAMPLE.gov:443/b.xml"), "https://EXAMPLE.gov:443/b.xml")
        self.assertEqual(adapters.follow_url("http://example.gov/s.xml", "https://example.gov/c.xml"), "https://example.gov/c.xml",
                         "http may move up to https")
        for bad in ("http://example.gov/b.xml", "https://example.gov:8443/x", "https://example.gov:99999/x", "file:///etc/hostname", "ftp://example.gov/x", "data:text/plain,x", "https://evil.example/",
                    "http://169.254.169.254/latest/", "//example.gov/x", "", None, 7):
            self.assertIsNone(adapters.follow_url(route, bad), bad)

    def test_kinds_per_adapter_cover_the_schema_kind_enum(self):
        schema = jsonio.load_json(ROOT / "schema" / "source.schema.json")
        kinds = set(schema["$defs"]["route"]["properties"]["kind"]["enum"])
        self.assertEqual({k for ks in adapters.ALLOWED_KINDS.values() for k in ks}, kinds)
        self.assertEqual(set(adapters.ALLOWED_KINDS), set(adapters.REQUIRED_PARAMS))
        self.assertEqual(adapters.lint_route({"adapter": "page", "kind": "rss", "url": "u", "params": {}}),
                         ["route u: kind 'rss' does not fit adapter 'page'"])
        self.assertEqual(adapters.lint_route({"adapter": "json-api", "kind": "json-api", "url": "u"}), [], "unconfigured, not wrong")

    def test_every_adapter_refuses_an_unknown_param(self):
        from feeder.schema_validate import validate
        schema = jsonio.load_json(ROOT / "schema" / "source.schema.json")
        base = jsonio.load_json(ROOT / "tests" / "fixtures" / "schema" / "source_example.json")
        minimal = {"feed": {}, "page": {"item_selector": "li"}, "sitemap": {}, "xml": {"item_path": "e", "fields": {"title": "t"}},
                   "json-api": {"items_path": "r[]", "fields": {"title": "t"}}, "urllist": {}, "artifact": {}}
        for adapter in schema["$defs"]["route"]["properties"]["adapter"]["enum"]:
            with self.subTest(adapter=adapter):
                doc = dict(base, routes=[{"adapter": adapter, "kind": adapters.ALLOWED_KINDS[adapter][0], "url": "https://example.gov/x",
                                          "params": dict(minimal[adapter], bogus=1)}])
                self.assertEqual(validate(doc, schema), ["$.routes[0].params: unexpected property 'bogus'"])

    def test_live_proof(self):
        good = {"source_label": "live", "http_status": 200, "result": "ok", "tried_at": "2026-10-19T00:00:00Z", "sample": {"title": "t", "link": None, "published": None}}
        self.assertTrue(adapters.live_proof(good))
        for bad in (dict(good, sample=None), dict(good, http_status=304), dict(good, source_label="archive"), dict(good, result="empty"), None):
            self.assertFalse(adapters.live_proof(bad))

    def test_follow_limit(self):
        self.assertEqual(adapters.follow_limit({"params": {}}), adapters.DEFAULT_FOLLOW_LIMIT)
        self.assertEqual(adapters.follow_limit({"params": {"max_sitemaps": 7}}), 7)
        self.assertEqual(adapters.follow_limit({"params": {"max_pages": 2}}), 2)
        self.assertEqual(adapters.follow_limit({"params": {"max_pages": 0}}), 0, "0 switches following off")
        self.assertEqual(adapters.follow_limit({"params": {"max_pages": True}}), adapters.DEFAULT_FOLLOW_LIMIT)


if __name__ == "__main__":
    unittest.main()
