"""One test per classification row, plus the host helpers."""
import unittest

from feeder import classify


class RowTests(unittest.TestCase):
    def assertRow(self, result, rule, authority, tier):
        self.assertEqual((result.rule, result.authority, result.tier), (rule, authority, tier))
        self.assertEqual(result.classification_review, "pending")
        self.assertEqual(result.as_dict()["classification_review"], "pending")

    def test_row_government(self):
        for url in ("https://www.govinfo.gov/rss/fr.xml", "https://www.cisa.gov/news.xml",
                    "https://www.army.mil/rss", "https://www.gov.uk/feed", "https://www.legislation.gov.au/x",
                    "https://eur-lex.europa.eu/rss", "https://www.myflorida.com/news", "https://www.flrules.org/",
                    "https://www.fldoe.org/news", "https://www.leg.state.fl.us/x"):
            self.assertRow(classify.classify(url), "government", "primary", "canonical")

    def test_row_standards_or_court(self):
        for url in ("https://www.nist.gov/news-events/news/rss.xml", "https://www.iso.org/news",
                    "https://www.pcisecuritystandards.org/rss", "https://www.supremecourt.gov/rss",
                    "https://www.ietf.org/blog/feed"):
            result = classify.classify(url)
            self.assertEqual((result.authority, result.tier), ("primary", "canonical"))
            self.assertIn(result.rule, ("government", "standards_or_court"))
        self.assertRow(classify.classify("https://www.iso.org/news"), "standards_or_court", "primary", "canonical")
        self.assertRow(classify.classify("https://www.pcisecuritystandards.org/rss"), "standards_or_court",
                       "primary", "canonical")

    def test_row_vendor_self(self):
        result = classify.classify("https://blog.example.com/security-advisories.xml",
                                   publisher_homepage="https://www.example.com/", about_own_products=True)
        self.assertRow(result, "vendor_self", "primary", "vendor")
        # Same domain but not about its own products: a publisher on its own domain is news.
        self.assertRow(classify.classify("https://blog.example.com/feed.xml",
                                         publisher_homepage="https://www.example.com/"), "default", "secondary", "news")
        # About its products but hosted elsewhere (a third-party blog platform): news.
        self.assertRow(classify.classify("https://example.substack.com/feed",
                                         publisher_homepage="https://www.example.com/", about_own_products=True),
                       "default", "secondary", "news")

    def test_row_default(self):
        self.assertRow(classify.classify("https://edsecledger.com/rss.xml"), "default", "secondary", "news")
        self.assertRow(classify.classify("https://www.example.co.uk/feed"), "default", "secondary", "news")
        self.assertRow(classify.classify("not a url"), "default", "secondary", "news")

    def test_government_suffix_is_not_fooled_by_lookalikes(self):
        self.assertFalse(classify.is_government("www.notgov.com"))
        self.assertFalse(classify.is_government("gov.example.com"))
        self.assertFalse(classify.is_government("example.gov.evil.com"))
        self.assertTrue(classify.is_government("example.gov"))
        self.assertTrue(classify.is_government("sub.example.gov.br"))

    def test_helpers(self):
        self.assertEqual(classify.host_of("HTTPS://WWW.Example.COM:443/x"), "www.example.com")
        self.assertEqual(classify.registrable_domain("a.b.example.com"), "example.com")
        self.assertEqual(classify.registrable_domain("news.bbc.co.uk"), "bbc.co.uk")
        self.assertEqual(len(classify.RULES), 4)
        self.assertEqual([r[0] for r in classify.RULES], ["government", "standards_or_court", "vendor_self", "default"])


if __name__ == "__main__":
    unittest.main()
