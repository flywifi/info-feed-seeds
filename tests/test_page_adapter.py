"""The page adapter: listing pages without a feed, read through a small CSS subset. The fixtures keep the
structure of the FedRAMP changelog and the PCI SSC blog index as fetched on 2026-10-26, with bylines
and images removed."""
import unittest

from feeder import adapters
from feeder.adapters import page
from tests.helpers import ROOT

PAGES = ROOT / "tests" / "fixtures" / "pages"
FEDRAMP = {"item_selector": "div.fiscal-year-container li", "title_selector": ".", "link_selector": "a@href",
           "date_selector": "span.entry-date"}
PCI = {"item_selector": "div.latest-post, div.post-item", "title_selector": "h3 a", "link_selector": "h3 a@href",
       "date_selector": "div.post-details-meta", "date_regex": r"\d{1,2} [A-Za-z]{3}, \d{4}", "date_format": "%d %b, %Y",
       "summary_selector": "div.post-body"}


def parse(body, params, url="https://example.gov/list"):
    return page.parse(body.encode("utf-8") if isinstance(body, str) else body, url=url, params=params)


class FixtureTests(unittest.TestCase):
    def test_fedramp_changelog_entries(self):
        parsed = parse((PAGES / "fedramp_changelog.html").read_bytes(), FEDRAMP, "https://www.fedramp.gov/changelog/")
        self.assertEqual(parsed["kind"], "page")
        self.assertEqual(parsed["follow"], [])
        self.assertEqual([i.published for i in parsed["items"]],
                         ["2026-10-21T00:00:00Z", "2026-10-15T00:00:00Z", "2026-10-14T00:00:00Z", "2026-10-08T00:00:00Z",
                          "2026-09-13T00:00:00Z"])
        first = parsed["items"][0]
        self.assertEqual(first.link, "https://www.fedramp.gov/marketplace/products", "a relative link resolves")
        self.assertTrue(first.title.startswith("2026-10-21 Marketplace now publicly displays 20x Pilot certifications"))
        self.assertEqual(first.guid, first.link)

    def test_pci_ssc_blog_index(self):
        parsed = parse((PAGES / "pci_ssc_blog.html").read_bytes(), PCI, "https://blog.pcisecuritystandards.org/")
        self.assertEqual([(i.title, i.published) for i in parsed["items"]], [
            ("Build Your Payment Security Expertise with PCI SSC eLearning", "2026-10-20T00:00:00Z"),
            ("The AI Exchange: Innovators in Payment Security Featuring AuditSafe", "2026-10-19T00:00:00Z"),
            ("The Quantum Leap: Preparing for Post Quantum Cryptography Featuring Utimaco Inc.", "2026-10-15T00:00:00Z")],
            "one selector group per layout, items in document order")
        self.assertEqual(parsed["items"][1].link,
                         "https://blog.pcisecuritystandards.org/the-ai-exchange-innovators-in-payment-security-featuring-auditsafe")
        self.assertTrue(parsed["items"][0].summary.startswith("PCI Security Standards Council (PCI SSC) eLearning gives"))

    def test_the_registry_reads_page_routes(self):
        self.assertIs(adapters.ADAPTERS["page"], page.parse)
        body = (PAGES / "pci_ssc_blog.html").read_bytes()
        self.assertEqual(len(adapters.check_result(adapters.ADAPTERS["page"](body, url="https://blog.pcisecuritystandards.org/",
                                                                             params=PCI))["items"]), 3)


class SelectorTests(unittest.TestCase):
    def test_unsupported_selectors_are_refused_before_parsing(self):
        for sel in ("li > a", "a[href]", "li:first-child", "", "ul,", "UL"):
            with self.subTest(sel=sel), self.assertRaises(page.PageParseError):
                parse("<ul><li><a href='/a'>A</a></li></ul>", {"item_selector": sel})

    def test_a_route_without_item_selector_or_with_a_bad_regex_is_refused(self):
        with self.assertRaises(adapters.AdapterError):
            parse("<li>x</li>", {})
        with self.assertRaises(page.PageParseError):
            parse("<li>x</li>", {"item_selector": "li", "date_regex": "("})

    def test_id_class_and_descendant_steps(self):
        html = ("<div id='main'><ul class='news list'><li><a href='/one'>One</a></li></ul></div>"
                "<ul class='news'><li><a href='/two'>Two</a></li></ul>")
        self.assertEqual([i.title for i in parse(html, {"item_selector": "div#main ul.news.list li"})["items"]], ["One"])
        self.assertEqual([i.link for i in parse(html, {"item_selector": "ul.news li"})["items"]],
                         ["https://example.gov/one", "https://example.gov/two"])

    def test_script_and_style_text_is_never_read(self):
        html = "<ul><li><script>var a = '<li>fake</li>';</script><style>li{}</style><a href='/x'>Real</a></li></ul>"
        self.assertEqual([i.title for i in parse(html, {"item_selector": "li"})["items"]], ["Real"])

    def test_entities_decode_and_empty_entries_are_skipped(self):
        html = "<ul><li><a href='/a?x=1&amp;y=2'>Tom &amp; Jerry</a></li><li></li></ul>"
        items = parse(html, {"item_selector": "li"})["items"]
        self.assertEqual([(i.title, i.link) for i in items], [("Tom & Jerry", "https://example.gov/a?x=1&y=2")])

    def test_a_link_less_entry_is_keyed_by_its_title(self):
        items = parse("<ul><li>Typo fixes</li></ul>", {"item_selector": "li", "title_selector": "."})["items"]
        self.assertEqual((items[0].guid, items[0].link, items[0].title), ("Typo fixes", None, "Typo fixes"))

    def test_an_empty_page_has_no_items(self):
        self.assertEqual(parse("", {"item_selector": "li"})["items"], [])

    def test_a_dash_in_page_text_stays_raw(self):
        title = "Release notes " + "\u2014" + " October"
        self.assertEqual(parse(f"<ul><li><a href='/r'>{title}</a></li></ul>", {"item_selector": "li"})["items"][0].title, title)


class DateTests(unittest.TestCase):
    def test_iso_dates_and_zones(self):
        self.assertEqual(page.parse_date("2026-10-21"), "2026-10-21T00:00:00Z")
        self.assertEqual(page.parse_date("2026-10-21T10:00:00+02:00"), "2026-10-21T08:00:00Z")
        self.assertEqual(page.parse_date("2026-10-21T10:00:00Z"), "2026-10-21T10:00:00Z")

    def test_formats_read_english_month_names_whatever_the_locale(self):
        self.assertEqual(page.parse_date("20 Oct, 2026", "%d %b, %Y"), "2026-10-20T00:00:00Z")
        self.assertEqual(page.parse_date("October 20, 2026", "%B %d, %Y"), "2026-10-20T00:00:00Z")
        self.assertIsNone(page.parse_date("20 Okt, 2026", "%d %b, %Y"), "a month that is not English is not guessed")

    def test_unparseable_dates_are_none(self):
        for text, fmt, rx in (("", None, None), ("soon", None, None), ("2026-13-01", None, None),
                              ("Posted today", "%d %b, %Y", r"\d{1,2} \w{3}, \d{4}"), ("31 Feb, 2026", "%d %b, %Y", None)):
            with self.subTest(text=text):
                self.assertIsNone(page.parse_date(text, fmt, rx))


if __name__ == "__main__":
    unittest.main()
