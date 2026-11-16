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


class XmlSafetyTests(unittest.TestCase):
    def test_a_gzip_sitemap_that_expands_past_the_body_cap_is_refused(self):
        bomb = gzip.compress(b"<urlset>" + b" " * (6 * 1024 * 1024) + b"</urlset>")
        with self.assertRaisesRegex(adapters.AdapterError, "expands past"):
            call("sitemap", bomb)

    def test_an_entity_declaration_after_a_long_comment_is_refused(self):
        body = b"<!--" + b"x" * 70000 + b'--><!DOCTYPE u [<!ENTITY a "b">]><urlset/>'
        with self.assertRaisesRegex(adapters.AdapterError, "DTD or entities"):
            call("sitemap", body)


class XmlEncodingTests(unittest.TestCase):
    def test_a_utf16_document_with_an_entity_is_refused(self):
        body = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE u [<!ENTITY a "b">]><urlset/>'.encode("utf-16")
        with self.assertRaisesRegex(adapters.AdapterError, "DTD or entities"):
            call("sitemap", body)

    def test_a_bare_doctype_is_refused(self):
        with self.assertRaisesRegex(adapters.AdapterError, "DTD or entities"):
            call("sitemap", b"<!DOCTYPE urlset>" + URLSET.split(b"?>", 1)[1])

    def test_a_cut_gzip_sitemap_is_a_bad_gzip_body(self):
        with self.assertRaisesRegex(adapters.AdapterError, "bad gzip body"):
            call("sitemap", gzip.compress(URLSET)[:-12])

    def test_a_gzip_sitemap_of_several_members_is_read_whole(self):
        half = len(URLSET) // 2
        items = call("sitemap", gzip.compress(URLSET[:half]) + gzip.compress(URLSET[half:]))["items"]
        self.assertEqual(len(items), 3)


class FeedDtdTests(unittest.TestCase):
    def test_a_utf16_feed_with_an_entity_is_refused(self):
        from feeder.adapters import feed
        body = ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE rss [<!ENTITY e "EXP">]>'
                '<rss version="2.0"><channel><title>T</title><item><title>&e;&e;</title></item></channel></rss>').encode("utf-16")
        with self.assertRaises(feed.FeedRefused):
            feed.parse(body, url="https://example.gov/f.xml", params={})

    def test_a_bot_wall_page_is_still_not_a_feed(self):
        from feeder.adapters import feed
        with self.assertRaises(feed.FeedParseError) as caught:
            feed.parse(b"<!DOCTYPE html><html><body>Just a moment</body></html>", url="https://example.gov/f.xml", params={})
        self.assertNotIsInstance(caught.exception, feed.FeedRefused)


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


class LabelTests(unittest.TestCase):
    FIELDS = {"title": "title", "link": "html_url", "deadline": "comments_close_on", "effective": "effective_on",
              "cfr_titles": "cfr_references[].title", "cfr_parts": "cfr_references[].part",
              "agencies": "agencies[].slug", "change_types": "type"}

    def one(self, **doc):
        body = json.dumps({"results": [dict({"title": "T", "html_url": "https://example.gov/d"}, **doc)]}).encode()
        parsed = files.parse_json_api(body, url="https://example.gov/api", params={"items_path": "results", "fields": self.FIELDS})
        return parsed["items"][0].extra

    def test_a_json_api_gives_its_labels(self):
        got = self.one(comments_close_on="2026-12-28", effective_on="2027-01-04", type="Proposed Rule",
                       cfr_references=[{"title": 32, "part": "170"}, {"title": 48, "part": "204"}, {"title": 32, "part": "170"}],
                       agencies=[{"slug": "defense-department"}, {"slug": "Defense Acquisition Regulations System"}])
        self.assertEqual(got, {"deadline": "2026-12-28", "effective": "2027-01-04", "cfr": ["32/170", "48/204"],
                               "agencies": ["defense-department", "defense-acquisition-regulations-system"],
                               "change_types": ["proposed-rule"]})

    def test_only_well_formed_label_values_are_kept(self):
        got = self.one(comments_close_on="2026-13-45", effective_on="soon", type=7,
                       cfr_references=[{"title": 99, "part": "1"}, {"title": 14, "part": "x y"}, {"part": "39"}, "junk"],
                       agencies=[{"slug": None}, {"slug": "  "}, 5])
        self.assertEqual(got, {})
        self.assertEqual(self.one(cfr_references={"title": 14, "part": "39"}, agencies="Federal Aviation Administration"), {},
                         "a path that reaches no list of entries gives no label")
        scalar = dict(self.FIELDS, cfr_titles="t", cfr_parts="p", agencies="agency")
        body = json.dumps({"results": [{"title": "T", "t": 14, "p": 39, "agency": "FAA"}]}).encode()
        item = files.parse_json_api(body, url="https://example.gov/api", params={"items_path": "results", "fields": scalar})["items"][0]
        self.assertEqual(item.extra, {"cfr": ["14/39"], "agencies": ["faa"]}, "two scalars make one reference")

    def test_without_label_paths_an_item_carries_none(self):
        body = json.dumps({"results": [{"title": "T", "comments_close_on": "2026-12-28"}]}).encode()
        item = files.parse_json_api(body, url="https://example.gov/api", params={"items_path": "results", "fields": {"title": "title"}})["items"][0]
        self.assertEqual(item.extra, {})


class EcfrTests(unittest.TestCase):
    ROWS = [
        {"starts_on": "2026-11-10", "type": "Section", "change_types": ["effective"],
         "hierarchy": {"title": "14", "part": "91", "section": "91.401", "appendix": None},
         "headings": {"title": "Aeronautics and Space", "chapter": "Federal Aviation Administration", "part": "General Operating and Flight Rules",
                      "section": "Applicability.", "appendix": None}},
        {"starts_on": "2026-11-09", "type": "Appendix", "change_types": ["effective", "cross_reference"],
         "hierarchy": {"title": "15", "part": "744", "section": None, "appendix": "Supplement No. 4 to Part 744"},
         "headings": {"chapter": "Bureau of Industry and Security", "part": "Control Policy", "appendix": "Entity List\n"}},
        {"starts_on": "not a date", "hierarchy": {"title": "26", "part": "1", "section": "1.401(a)-1"}, "headings": {"section": "Plans."}},
        {"starts_on": "2026-11-09", "hierarchy": {"title": "99", "part": "1", "section": "1.1"}},
        {"starts_on": "2026-11-06", "change_types": ["effective"], "hierarchy": {"title": "41", "part": "60-30", "section": "60-30.1"},
         "headings": {"part": "Rules of Practice for Administrative Proceedings", "section": "Applicability of rules."}},
        {"starts_on": "2026-11-09", "hierarchy": "junk"}, "junk",
    ]

    def test_one_item_per_changed_section_or_appendix(self):
        from feeder.adapters import ecfr
        parsed = ecfr.parse(json.dumps({"results": self.ROWS, "meta": {}}).encode(), url="https://www.ecfr.gov/api/search/v1/results")
        self.assertEqual((parsed["kind"], parsed["follow"]), ("ecfr", []))
        got = [(i.title, i.link, i.published, i.guid, i.summary, i.extra) for i in parsed["items"]]
        self.assertEqual(got, [
            ("14 CFR 91.401 Applicability.", "https://www.ecfr.gov/current/title-14/section-91.401", "2026-11-10T00:00:00Z",
             "ecfr:14:91.401:2026-11-10", "General Operating and Flight Rules; Federal Aviation Administration",
             {"cfr": ["14/91", "14/91.401"], "change_types": ["effective"]}),
            ("15 CFR part 744, Supplement No. 4 to Part 744 Entity List", "https://www.ecfr.gov/current/title-15/part-744",
             "2026-11-09T00:00:00Z", "ecfr:15:Supplement No. 4 to Part 744:2026-11-09", "Control Policy; Bureau of Industry and Security",
             {"cfr": ["15/744"], "change_types": ["effective", "cross-reference"]}),
            ("26 CFR 1.401(a)-1 Plans.", "https://www.ecfr.gov/current/title-26/section-1.401(a)-1", None,
             "ecfr:26:1.401(a)-1:undated", "", {"cfr": ["26/1"]}),
            ("41 CFR 60-30.1 Applicability of rules.", "https://www.ecfr.gov/current/title-41/section-60-30.1", "2026-11-06T00:00:00Z",
             "ecfr:41:60-30.1:2026-11-06", "Rules of Practice for Administrative Proceedings",
             {"cfr": ["41/60-30", "41/60-30.1"], "change_types": ["effective"]}),
        ], "a row without a real CFR title and part is skipped")

    def test_a_body_that_is_not_a_change_list_is_refused(self):
        from feeder.adapters import ecfr
        for body in (b"<html>", b"[]", b'{"results": "x"}', b'{"meta": {}}'):
            with self.subTest(body=body), self.assertRaises(adapters.AdapterError):
                ecfr.parse(body)
        self.assertIs(adapters.ADAPTERS["ecfr"], ecfr.parse)


if __name__ == "__main__":
    unittest.main()
