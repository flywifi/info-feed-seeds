"""The repository schemas validate their fixtures, and the mutations that must fail do fail."""
import copy
import unittest

from feeder import jsonio
from feeder.schema_validate import validate
from tests.helpers import FIXTURES, ROOT

SCHEMA_DIR = ROOT / "schema"


def load_schema(name):
    return jsonio.load_json(SCHEMA_DIR / name)


def load_fixture(name):
    return jsonio.load_json(FIXTURES / "schema" / name)


class SchemaFixtureTests(unittest.TestCase):
    def test_schema_files_are_canonical_json(self):
        for path in sorted(SCHEMA_DIR.glob("*.json")):
            self.assertTrue(jsonio.is_canonical(path), f"{path.name} is not in canonical form (run the writer)")

    def test_source_example_validates(self):
        self.assertEqual(validate(load_fixture("source_example.json"), load_schema("source.schema.json")), [])

    def test_source_mutations_fail(self):
        schema = load_schema("source.schema.json")
        base = load_fixture("source_example.json")

        bad_id = copy.deepcopy(base)
        bad_id["id"] = "Not Valid"
        self.assertTrue(validate(bad_id, schema))

        unknown = copy.deepcopy(base)
        unknown["notes"] = "private"
        self.assertTrue(validate(unknown, schema), "additional properties are refused")

        bad_route = copy.deepcopy(base)
        bad_route["routes"][0]["kind"] = "scrape"
        self.assertTrue(validate(bad_route, schema))

        no_routes = copy.deepcopy(base)
        no_routes["routes"] = []
        self.assertTrue(validate(no_routes, schema))

        bad_topic = copy.deepcopy(base)
        bad_topic["topics"] = ["Cyber Security"]
        self.assertTrue(validate(bad_topic, schema))

        auth_without_local = copy.deepcopy(base)
        auth_without_local["auth"] = {"credential": "govinfo-api-key"}
        self.assertTrue(validate(auth_without_local, schema), "auth requires runs_on local only")
        auth_without_local["runs_on"] = ["local"]
        self.assertEqual(validate(auth_without_local, schema), [])

        bad_credential = copy.deepcopy(auth_without_local)
        bad_credential["auth"] = {"credential": "Owner's key"}
        self.assertTrue(validate(bad_credential, schema))

        bad_discovery = copy.deepcopy(base)
        bad_discovery["discovered_from"]["kind"] = "guess"
        self.assertTrue(validate(bad_discovery, schema))

        bad_review = copy.deepcopy(base)
        bad_review["classification_review"] = "done"
        self.assertTrue(validate(bad_review, schema))

        no_category = copy.deepcopy(base)
        del no_category["category"]
        self.assertEqual(validate(no_category, schema), ["$: missing required property 'category'"])
        bad_category = copy.deepcopy(base)
        bad_category["subcategory"] = "Security Guidance"
        self.assertTrue(validate(bad_category, schema))
        bad_industry = copy.deepcopy(base)
        bad_industry["industries"] = ["Cyber"]
        self.assertTrue(validate(bad_industry, schema))

    def test_route_params_follow_the_adapter(self):
        schema = load_schema("source.schema.json")
        base = load_fixture("source_example.json")

        def with_route(route):
            doc = copy.deepcopy(base)
            doc["routes"].append(route)
            return doc

        for route in (
            {"adapter": "page", "kind": "page", "url": "https://example.gov/blog/", "params": {
                "item_selector": "div.post", "title_selector": "h3 a", "link_selector": "h3 a@href",
                "date_selector": "div.meta", "date_regex": "\\d{1,2} \\w{3}, \\d{4}", "date_format": "%d %b, %Y"}},
            {"adapter": "page", "kind": "page", "url": "https://example.gov/changelog/", "params": {"version_signal": "heading-date"}},
            {"adapter": "sitemap", "kind": "sitemap", "url": "https://example.gov/sitemap.xml", "params": {"max_sitemaps": 5, "lastmod_since": "2026-10-01"}},
            {"adapter": "xml", "kind": "xml", "url": "https://example.gov/catalog.xml", "params": {
                "item_path": "c:entry", "namespaces": {"c": "urn:example:cat"}, "fields": {"title": "c:name", "link": "c:url"}}},
            {"adapter": "json-api", "kind": "json-api", "url": "https://example.gov/api/documents.json", "params": {
                "items_path": "results[]", "fields": {"title": "title", "link": "html_url"}, "next_path": "next_page_url", "max_pages": 3}},
            {"adapter": "urllist", "kind": "urllist", "url": "https://example.gov/list.txt", "params": {}},
            {"adapter": "artifact", "kind": "artifact", "url": "https://example.gov/guide.pdf", "params": {"label": "Guide"}},
        ):
            with self.subTest(adapter=route["adapter"]):
                self.assertEqual(validate(with_route(route), schema), [])

        selectors_without_item = with_route({"adapter": "page", "kind": "page", "url": "https://example.gov/blog/",
                                             "params": {"title_selector": "h3"}})
        self.assertEqual(validate(selectors_without_item, schema),
                         ["$.routes[2].params: missing required property 'item_selector'"])
        unknown_feed_param = copy.deepcopy(base)
        unknown_feed_param["routes"][0]["params"] = {"x": 1}
        self.assertEqual(validate(unknown_feed_param, schema), ["$.routes[0].params: unexpected property 'x'"])
        too_many_pages = with_route({"adapter": "json-api", "kind": "json-api", "url": "https://example.gov/api.json",
                                     "params": {"items_path": "results[]", "fields": {"title": "t"}, "max_pages": 99}})
        self.assertTrue(validate(too_many_pages, schema))
        xml_without_item_path = with_route({"adapter": "xml", "kind": "xml", "url": "https://example.gov/c.xml",
                                            "params": {"fields": {"title": "t"}}})
        self.assertTrue(validate(xml_without_item_path, schema))
        bad_signal = with_route({"adapter": "page", "kind": "page", "url": "https://example.gov/c/", "params": {"version_signal": "guess"}})
        self.assertTrue(validate(bad_signal, schema))

    def test_methods_example_validates(self):
        self.assertEqual(validate(load_fixture("methods_example.json"), load_schema("methods.schema.json")), [])

    def test_methods_mutations_fail(self):
        schema = load_schema("methods.schema.json")
        base = load_fixture("methods_example.json")
        attempt = base["routes"][0]["attempts"][0]

        archive_without_capture = copy.deepcopy(base)
        archive_without_capture["routes"][0]["attempts"][0]["source_label"] = "archive"
        self.assertTrue(validate(archive_without_capture, schema), "archive needs a fetched capture_url")
        archive_without_capture["routes"][0]["attempts"][0]["capture_url"] = "https://web.archive.org/web/2/https://www.nist.gov/"
        self.assertEqual(validate(archive_without_capture, schema), [])

        bad_media = copy.deepcopy(base)
        bad_media["routes"][0]["attempts"][0]["media"] = [{"file": "grab.png", "sha256": "abc", "kind": "screenshot"}]
        self.assertTrue(validate(bad_media, schema))

        bad_time = copy.deepcopy(base)
        bad_time["routes"][0]["attempts"][0]["tried_at"] = "2026-10-20 12:00"
        self.assertTrue(validate(bad_time, schema))

        bad_result = copy.deepcopy(base)
        bad_result["routes"][0]["attempts"][0]["result"] = "maybe"
        self.assertTrue(validate(bad_result, schema))
        self.assertIn("source_label", attempt)

    def test_profile_categories(self):
        schema = load_schema("profile.schema.json")
        base = load_fixture("profile_example.json")
        self.assertEqual(validate(dict(base, categories=["regulatory", "attacks/breaches"]), schema), [])
        self.assertTrue(validate(dict(base, categories=[]), schema), "an empty list would select nothing")
        self.assertTrue(validate(dict(base, categories=["Attacks"]), schema))

    def test_profile_example_validates(self):
        self.assertEqual(validate(load_fixture("profile_example.json"), load_schema("profile.schema.json")), [])
        bad = load_fixture("profile_example.json")
        bad["daily_cap"] = 0
        self.assertTrue(validate(bad, load_schema("profile.schema.json")))

    def test_item_example_validates(self):
        self.assertEqual(validate(load_fixture("item_example.json"), load_schema("item.schema.json")), [])
        bad = load_fixture("item_example.json")
        bad["published"] = "2026-10-20"
        self.assertTrue(validate(bad, load_schema("item.schema.json")), "published must be a full UTC Z timestamp")
        bad["published"] = None
        bad["display"]["summary"] = "x" * 301
        self.assertTrue(validate(bad, load_schema("item.schema.json")))

    def test_fixture_topics_exist_in_registry(self):
        from feeder.topics import all_tags
        tags = all_tags()
        for name in ("source_example.json", "profile_example.json", "item_example.json"):
            for tag in load_fixture(name).get("topics", []):
                self.assertIn(tag, tags, f"{name} uses an unregistered topic {tag}")


if __name__ == "__main__":
    unittest.main()
