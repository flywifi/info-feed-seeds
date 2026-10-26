"""Sitemap, URL list, XML, JSON API and artifact adapters. The JSON shapes follow the Federal Register
documents API (results[], next_page_url) and the CISA KEV catalogue (vulnerabilities[], cveID)."""
import gzip
import json
import unittest

from feeder import adapters
from feeder.adapters import files

SM = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
URLSET = (f'<?xml version="1.0"?><urlset {SM}>'
          '<url><loc>https://example.gov/a</loc><lastmod>2026-10-20</lastmod></url>'
          '<url><loc>https://example.gov/news/b</loc><lastmod>2026-10-21T10:00:00+02:00</lastmod></url>'
          '<url><loc>https://example.gov/news/old</loc><lastmod>2025-01-01</lastmod></url>'
          '<url><loc> </loc></url></urlset>').encode()


def call(name, body, params=None, url="https://example.gov/x"):
    return adapters.check_result(adapters.ADAPTERS[name](body, url=url, params=params or {}))


class SitemapTests(unittest.TestCase):
    def test_urlset_items_dates_and_filters(self):
        items = call("sitemap", URLSET)["items"]
        self.assertEqual([(i.link, i.published) for i in items], [
            ("https://example.gov/a", "2026-10-20T00:00:00Z"), ("https://example.gov/news/b", "2026-10-21T08:00:00Z"),
            ("https://example.gov/news/old", "2025-01-01T00:00:00Z")])
        kept = call("sitemap", URLSET, {"path_prefix": "/news/", "lastmod_since": "2026-01-01"})["items"]
        self.assertEqual([i.link for i in kept], ["https://example.gov/news/b"])

    def test_gzip_sitemapindex_yields_follow_urls(self):
        body = gzip.compress(f'<sitemapindex {SM}><sitemap><loc>https://example.gov/s1.xml</loc></sitemap>'
                             f'<sitemap><loc>https://example.gov/s2.xml</loc></sitemap></sitemapindex>'.encode())
        parsed = call("sitemap", body)
        self.assertEqual((parsed["items"], parsed["follow"]), ([], ["https://example.gov/s1.xml", "https://example.gov/s2.xml"]))

    def test_refusals(self):
        for body in (b"<html></html>", b'<!DOCTYPE x [<!ENTITY a "b">]><urlset/>', URLSET[:60], b"\x1f\x8bnot gzip"):
            with self.subTest(body=body[:20]), self.assertRaises(adapters.AdapterError):
                call("sitemap", body)


class UrlListTests(unittest.TestCase):
    def test_comments_non_http_and_duplicates_are_dropped(self):
        body = b"\xef\xbb\xbfhttps://example.gov/a\r\n# note\nftp://example.gov/f\nhttps://example.gov/b # why\nhttps://example.gov/a\n\n"
        self.assertEqual([i.link for i in call("urllist", body)["items"]], ["https://example.gov/a", "https://example.gov/b"])


class XmlTests(unittest.TestCase):
    BODY = (b'<?xml version="1.0"?><c:catalog xmlns:c="urn:example"><c:entry id="E1" updated="2026-10-19">'
            b'<c:name>First <b>entry</b></c:name><c:url>https://example.gov/e1</c:url></c:entry><c:entry/></c:catalog>')
    PARAMS = {"item_path": "c:entry", "namespaces": {"c": "urn:example"},
              "fields": {"guid": "@id", "title": "c:name", "link": "c:url", "published": "@updated"}}

    def test_fields_attributes_and_empty_entries(self):
        items = call("xml", self.BODY, self.PARAMS)["items"]
        self.assertEqual([(i.guid, i.title, i.link, i.published) for i in items],
                         [("E1", "First entry", "https://example.gov/e1", "2026-10-19T00:00:00Z")])

    def test_missing_params_and_bad_paths_are_refused(self):
        for params in ({"item_path": "c:entry"}, {"fields": {"title": "t"}}, dict(self.PARAMS, item_path="x:entry")):
            with self.subTest(params=params), self.assertRaises(adapters.AdapterError):
                call("xml", self.BODY, params)


class JsonApiTests(unittest.TestCase):
    def test_federal_register_shape_with_next_page(self):
        doc = {"count": 2, "next_page_url": "https://www.federalregister.gov/api/v1/documents.json?page=2", "results": [
            {"title": "Rule one", "html_url": "https://www.federalregister.gov/d/1", "publication_date": "2026-10-20",
             "document_number": "2026-1", "abstract": "Summary one"},
            {"title": None, "html_url": None}]}
        parsed = call("json-api", json.dumps(doc).encode(), {"items_path": "results[]", "next_path": "next_page_url", "fields": {
            "title": "title", "link": "html_url", "guid": "document_number", "published": "publication_date", "summary": "abstract"}})
        self.assertEqual([(i.guid, i.title, i.published, i.summary) for i in parsed["items"]],
                         [("2026-1", "Rule one", "2026-10-20T00:00:00Z", "Summary one")])
        self.assertEqual(parsed["follow"], [doc["next_page_url"]])

    def test_kev_shape_keys_items_by_cve(self):
        doc = {"vulnerabilities": [{"cveID": "CVE-2026-0001", "vulnerabilityName": "Example flaw", "dateAdded": "2026-10-21"}]}
        items = call("json-api", json.dumps(doc).encode(), {"items_path": "vulnerabilities", "fields": {
            "guid": "cveID", "title": "vulnerabilityName", "published": "dateAdded"}})["items"]
        self.assertEqual((items[0].guid, items[0].link, items[0].title), ("CVE-2026-0001", None, "Example flaw"))

    def test_json_path_maps_over_lists(self):
        self.assertEqual(files.json_path({"a": [{"b": 1}, {"b": 2}]}, "a[].b"), [1, 2])
        self.assertIsNone(files.json_path({"a": 1}, "a.b"))

    def test_refusals(self):
        for body, params in ((b"not json", {"items_path": "r", "fields": {"title": "t"}}),
                             (b'{"r": 1}', {"items_path": "r", "fields": {"title": "t"}}), (b"{}", {"items_path": "r"})):
            with self.subTest(body=body), self.assertRaises(adapters.AdapterError):
                call("json-api", body, params)


class ArtifactTests(unittest.TestCase):
    def test_one_item_keyed_by_content(self):
        a = call("artifact", b"v1", {"label": "Control catalog"}, url="https://example.gov/cat.json")["items"][0]
        b = call("artifact", b"v2", url="https://example.gov/cat.json")["items"][0]
        self.assertEqual(a.title, "File changed: Control catalog")
        self.assertEqual(b.title, "File changed: cat.json")
        self.assertNotEqual(a.guid, b.guid)
        self.assertEqual(a.guid, call("artifact", b"v1", url="https://example.gov/cat.json")["items"][0].guid,
                         "the same content gives the same key, so the store adds nothing")


if __name__ == "__main__":
    unittest.main()
