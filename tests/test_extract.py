"""Tag readers on planted item text: CVE ids, severity, exploitation, vendors and deadlines, and the
vendor registry's own checks."""
import tempfile
import unittest
from pathlib import Path

from feeder import extract, jsonio
from tests.helpers import ROOT

NAMES = extract.load_vendors(ROOT / "schema" / "vendors.json")


class CveTests(unittest.TestCase):
    def test_ids_are_upper_case_once_each_in_order(self):
        self.assertEqual(extract.cve_ids("cve-2026-1234 and CVE-2026-109618, then CVE-2026-1234 again"),
                         ["CVE-2026-1234", "CVE-2026-109618"])
        self.assertEqual(extract.cve_ids("CVE-26-1 and CVE-2026-123 are not ids"), [])


class SeverityTests(unittest.TestCase):
    def test_words_in_a_severity_context(self):
        self.assertEqual(extract.severity("Patch released (High)"), "high")
        self.assertEqual(extract.severity("a critical-severity flaw"), "critical")
        self.assertEqual(extract.severity("Severity: medium"), "medium")
        self.assertEqual(extract.severity("rated low by the vendor"), "low")

    def test_a_cvss_score_sets_the_band_and_the_highest_wins(self):
        self.assertEqual(extract.severity("(Medium) CVSS v3.1 base score: 9.8"), "critical")
        self.assertEqual(extract.severity("CVSS 7.5"), "high")
        self.assertEqual(extract.severity("CVSS score of 4.0"), "medium")
        for score, word in ((0.0, "none"), (3.9, "low"), (4.0, "medium"), (6.9, "medium"), (8.9, "high"), (10.0, "critical")):
            self.assertEqual(extract.severity_from_score(score), word)
        self.assertIsNone(extract.severity_from_score(10.1))

    def test_ordinary_words_are_not_a_severity(self):
        for text in ("critical infrastructure operators", "high availability clusters", "a low-cost device", ""):
            self.assertIsNone(extract.severity(text), text)


class ExploitedTests(unittest.TestCase):
    def test_exploitation_phrases(self):
        for text in ("actively exploited", "Exploited in the wild", "Active Zero-Day: CVE-2026-1", "exploitation has been observed"):
            self.assertTrue(extract.exploited(text), text)
        self.assertFalse(extract.exploited("a proof of concept was published"))


class VendorTests(unittest.TestCase):
    def test_whole_words_with_their_exact_capitalisation(self):
        self.assertEqual(extract.vendors("FortiOS and FortiGate fixes", NAMES), ["fortinet"])
        self.assertEqual(extract.vendors("Cisco IOS XE and Apple iOS", NAMES), ["apple", "cisco"])
        self.assertEqual(extract.vendors("ios xe in lower case and fortinetwork", NAMES), [])

    def test_registry_checks(self):
        self.assertEqual(extract.vendor_problems(ROOT / "schema" / "vendors.json"), [])
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "vendors.json"
            jsonio.dump_json(bad, {"vendors": {"Bad Id": {"aliases": ["X"]}, "a": {"aliases": []},
                                               "b": {"aliases": ["Shared"]}, "c": {"aliases": ["Shared"]}}})
            problems = extract.vendor_problems(bad)
        self.assertEqual(len(problems), 4, problems)


class DeadlineTests(unittest.TestCase):
    def test_dates_tied_to_a_deadline(self):
        text = ("Comments are due March 31, 2027. Effective date: 1 July 2027. The deadline 2027-01-15 stands. "
                "Published October 1, 2026.")
        self.assertEqual(extract.deadlines(text), ["2027-03-31", "2027-07-01", "2027-01-15"])

    def test_impossible_or_foreign_dates_are_not_guessed(self):
        self.assertEqual(extract.deadlines("Deadline: February 30, 2027. Due date 1 Juli 2027."), [])


class TagsTests(unittest.TestCase):
    def test_one_item(self):
        got = extract.tags("FortiOS heap overflow (Critical)", "CVE-2026-24858 is actively exploited. Patches must be "
                           "applied by November 10, 2026.", NAMES)
        self.assertEqual(got, {"cves": ["CVE-2026-24858"], "severity": "critical", "severity_rank": 4, "exploited": True,
                               "vendors": ["fortinet"], "deadline": "2026-11-10"})
        self.assertEqual(extract.tags("Workshop announced", "", NAMES),
                         {"cves": [], "severity": None, "severity_rank": None, "exploited": False, "vendors": [], "deadline": None})


if __name__ == "__main__":
    unittest.main()
